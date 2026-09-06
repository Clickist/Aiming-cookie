"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import {
  authorizeProviderProfile,
  cancelProviderAuthOperation,
  createProviderProfile,
  deleteProviderCredential,
  deleteProviderProfile,
  discoverCustomProviderModels,
  getProviderAuthOperation,
  setDefaultProviderProfile,
  setProviderApiKey,
  submitProviderAuthInput,
  takeProviderAuthResult,
  testProviderProfile,
  testProviderProfileDraft,
  updateProviderProfile,
} from "@/lib/api";
import {
  isAuthTerminal,
  isCustomProviderKind,
  useCustomModelDiscovery,
} from "@/lib/provider-helpers";
import {
  WIZARD_TYPES,
  buildWizardPayload,
  emptyWizardDraft,
  isWizardCustom,
  previewBuiltinRequestUrl,
  previewCustomRequestUrl,
  wizardCatalogProvider,
  wizardDefaultName,
  type WizardDraft,
} from "@/lib/provider-wizard";
import type {
  ProviderAuthOperation,
  ProviderCatalogV1,
  ProviderProfile,
  ProviderProfileCreate,
  ProviderProfileState,
  ProviderReasoningEffort,
} from "@/lib/types";
import { Badge, Button, Dialog, Field, FieldControl, Loading, Notice, Panel, Status } from "@/ui/primitives";

// Raycast 式先验后存：干跑结果绑定提交时的表单指纹，
// 表单任何变动都会让旧结论失效并回到未验证态。
type DraftCheck =
  | { phase: "idle" }
  | { phase: "checking"; fingerprint: string }
  | { phase: "done"; fingerprint: string; passed: boolean; message: string };

type WizardStep = 1 | 2 | 3 | 4;

type ConfirmAction = {
  title: string;
  impact: string;
  run: () => Promise<void>;
} | null;

const WIZARD_STEP_LABELS = ["选类型", "名称与端点", "API Key", "测试连接"] as const;

function providerStateLabel(status: ProviderProfileState): string {
  switch (status) {
    case "unconfigured": return "未配置";
    case "auth_expired": return "认证已过期";
    case "needs_reauth": return "需要重新认证";
    case "ready": return "可用";
    case "model_unavailable": return "模型不可用";
    case "connection_failed": return "连接失败";
  }
}

function providerStatusTone(status: ProviderProfileState): "success" | "warning" | "error" | "neutral" {
  if (status === "ready") return "success";
  if (status === "needs_reauth" || status === "auth_expired") return "warning";
  if (status === "connection_failed" || status === "model_unavailable") return "error";
  return "neutral";
}

function authOperationLabel(status: ProviderAuthOperation["status"]): string {
  switch (status) {
    case "running": return "正在连接 Provider";
    case "awaiting_input": return "等待认证输入";
    case "succeeded": return "授权成功";
    case "failed": return "授权失败";
    case "cancelled": return "已取消";
    case "timed_out": return "已超时";
  }
}

function providerTypeLabel(profile: ProviderProfile, catalog: ProviderCatalogV1 | null): string {
  if (isCustomProviderKind(profile.kind)) {
    return profile.kind === "custom_anthropic_compatible" ? "Anthropic 兼容" : "OpenAI 兼容";
  }
  const entry = catalog?.providers.find((provider) => provider.provider_id === profile.provider_id);
  return entry?.provider_name ?? profile.provider_id ?? "内置 Provider";
}

function shortTime(value: string | null | undefined): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(date);
}

/** 第 1–3 步各自的「下一步」门槛（第 4 步的门槛是测连通过）。 */
function wizardStepReady(step: WizardStep, draft: WizardDraft, builtinAvailable: boolean): boolean {
  if (step === 1) return isWizardCustom(draft.typeId) || builtinAvailable;
  if (step === 2) return isWizardCustom(draft.typeId) ? Boolean(draft.baseUrl.trim()) : true;
  return Boolean(draft.modelId.trim() && (!isWizardCustom(draft.typeId) || draft.apiKey.trim()));
}

export function ProviderSettingsSection({
  profiles,
  catalog,
  loading,
  refresh,
  notify,
}: {
  profiles: ProviderProfile[];
  catalog: ProviderCatalogV1 | null;
  loading: boolean;
  refresh: (force?: boolean) => Promise<void>;
  notify: (message: string) => void;
}) {
  // 当前使用的档 = 默认档（与 Coach 实际解析一致），无默认时回退第一档。
  const activeProfile = useMemo(
    () => profiles.find((profile) => profile.is_default) ?? profiles[0] ?? null,
    [profiles],
  );
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const selectedProfile = useMemo(
    () => profiles.find((profile) => profile.id === selectedId) ?? activeProfile,
    [activeProfile, profiles, selectedId],
  );

  const [confirmAction, setConfirmAction] = useState<ConfirmAction>(null);
  const [switchingProvider, setSwitchingProvider] = useState(false);
  const [nameDraft, setNameDraft] = useState<string | null>(null);
  const [credentialDraft, setCredentialDraft] = useState("");
  const [keyDraftVisible, setKeyDraftVisible] = useState(false);
  const [keyDraftOpen, setKeyDraftOpen] = useState(false);
  const [testingConnection, setTestingConnection] = useState(false);
  const [lastTest, setLastTest] = useState<{ passed: boolean; message: string } | null>(null);

  // ── 添加向导状态（模态四步，最后一步硬门槛） ─────────────────
  const [wizardOpen, setWizardOpen] = useState(false);
  const [wizardStep, setWizardStep] = useState<WizardStep>(1);
  const [wizardDraft, setWizardDraft] = useState<WizardDraft>(emptyWizardDraft);
  const [wizardShowKey, setWizardShowKey] = useState(false);
  const [wizardCheck, setWizardCheck] = useState<DraftCheck>({ phase: "idle" });
  const [createdProfile, setCreatedProfile] = useState<ProviderProfile | null>(null);
  const wizardCheckAbort = useRef<AbortController | null>(null);

  // ── OAuth 授权流程（已有档，先保存后授权） ───────────────────
  const [authOperation, setAuthOperation] = useState<ProviderAuthOperation | null>(null);
  const [authProfileId, setAuthProfileId] = useState<number | null>(null);
  const [authPromptValue, setAuthPromptValue] = useState("");

  const wizardCustom = isWizardCustom(wizardDraft.typeId);
  const wizardCatalogEntry = wizardCustom ? undefined : wizardCatalogProvider(catalog, wizardDraft.typeId);

  const customDiscovery = useCustomModelDiscovery({
    baseUrl: wizardDraft.baseUrl,
    apiKey: wizardDraft.apiKey,
    enabled: wizardOpen && wizardCustom,
    discover: discoverCustomProviderModels,
  });
  const {
    models: customModels,
    state: customModelState,
    message: customModelMessage,
    protocolConfirmed: customProtocolConfirmed,
    kind: customKind,
  } = customDiscovery;

  const selectedCustomModel = customModels.find((model) => model.model_id === wizardDraft.modelId);
  // 内置目录带 reasoning 元数据：仅当选中的模型确认支持推理时，向导才露出
  // 思考力度旋钮。自定义 Provider 的发现结果没有该元数据，保持未设置（默认）。
  const wizardModelIsReasoning = !wizardCustom
    && wizardCatalogEntry?.models.find((model) => model.model_id === wizardDraft.modelId)?.reasoning === true;
  // 干跑与入库共用同一份候选 payload：「测试连接」验的就是将来要存的内容。
  const wizardPayload: ProviderProfileCreate | null = buildWizardPayload(wizardDraft, {
    isCustom: wizardCustom,
    customKind,
    selectedCustomModel,
    builtinModelIsReasoning: wizardModelIsReasoning,
    builtinProviderAvailable: Boolean(wizardCatalogEntry),
    defaultName: wizardDefaultName(catalog, wizardDraft.typeId),
    isFirstProfile: profiles.length === 0,
  });
  const wizardFingerprint = wizardPayload ? JSON.stringify(wizardPayload) : null;
  const wizardCheckingNow = wizardCheck.phase === "checking" && wizardCheck.fingerprint === wizardFingerprint;
  const wizardVerified = wizardCheck.phase === "done"
    && wizardCheck.passed
    && wizardCheck.fingerprint === wizardFingerprint;

  // 表单任何变动都会改变指纹：中止在途检查，回到未验证态并锁住完成。
  useEffect(() => {
    if (wizardCheck.phase === "checking" && wizardCheck.fingerprint !== wizardFingerprint) {
      wizardCheckAbort.current?.abort();
    }
  }, [wizardCheck, wizardFingerprint]);

  useEffect(() => () => wizardCheckAbort.current?.abort(), []);

  const openWizard = () => {
    setWizardDraft(emptyWizardDraft());
    setWizardStep(1);
    setWizardShowKey(false);
    setWizardCheck({ phase: "idle" });
    setCreatedProfile(null);
    customDiscovery.reset();
    setWizardOpen(true);
  };

  const closeWizard = () => {
    wizardCheckAbort.current?.abort();
    setWizardOpen(false);
  };

  const wizardSetDraft = (patch: Partial<WizardDraft>) => {
    if (patch.typeId !== undefined || patch.baseUrl !== undefined || patch.apiKey !== undefined) {
      // 类型切换 / 自定义端点与 key 变动会令协议探测与检查结论失效。
      customDiscovery.reset();
      setWizardCheck({ phase: "idle" });
    }
    setWizardDraft((current) => ({ ...current, ...patch }));
  };

  const runWizardCheck = async () => {
    if (wizardCheckingNow) {
      wizardCheckAbort.current?.abort(); // 再次点击即取消，不阻塞离开向导。
      return;
    }
    // 冻结本次检查对应的 payload 与指纹：期间表单再变，结论也不解锁完成。
    const payload = wizardPayload;
    const fingerprint = wizardFingerprint;
    if (!payload || !fingerprint) return;
    const controller = new AbortController();
    wizardCheckAbort.current = controller;
    setWizardCheck({ phase: "checking", fingerprint });
    try {
      const status = await testProviderProfileDraft(payload, { signal: controller.signal });
      setWizardCheck({
        phase: "done",
        fingerprint,
        passed: status.status === "ready",
        message: status.status === "ready"
          ? `连接成功 · ${payload.name}`
          : `${status.message}。请核对 API Key、Base URL 与所选模型后重试。`,
      });
    } catch (error) {
      if (controller.signal.aborted) {
        setWizardCheck({ phase: "idle" });
        return;
      }
      setWizardCheck({
        phase: "done",
        fingerprint,
        passed: false,
        message: `${error instanceof Error && error.message ? error.message : "无法连接本地服务"}。请确认本地服务运行正常后重试。`,
      });
    } finally {
      if (wizardCheckAbort.current === controller) wizardCheckAbort.current = null;
    }
  };

  const finishWizard = async () => {
    if (!wizardPayload || !wizardVerified) return;
    const created = await createProviderProfile(wizardPayload);
    setWizardDraft((current) => ({ ...current, apiKey: "" }));
    setWizardCheck({ phase: "idle" });
    setCreatedProfile(created);
    await refresh(true);
  };

  const makeActive = async (profileId: number) => {
    if (switchingProvider) return;
    setSwitchingProvider(true);
    try {
      await setDefaultProviderProfile(profileId);
      await refresh(true);
    } catch {
      notify("默认 Provider 未能更新。");
    } finally {
      setSwitchingProvider(false);
    }
  };

  const testConnection = async (profile: ProviderProfile) => {
    setTestingConnection(true);
    try {
      const status = await testProviderProfile(profile.id);
      setLastTest({ passed: status.status === "ready", message: `${profile.name}：${status.message}` });
      await refresh(true);
    } catch {
      // 测连失败只标错误，不删档案。
      setLastTest({ passed: false, message: `${profile.name}：连接测试失败，请检查 Provider 与网络。` });
    } finally {
      setTestingConnection(false);
    }
  };

  const commitNameEdit = async (profile: ProviderProfile) => {
    const nextName = nameDraft?.trim() ?? "";
    setNameDraft(null);
    if (!nextName || nextName === profile.name) return;
    try {
      // PUT 是整档更新；请求体不带新 api_key 时 sidecar 保留现有 credential。
      await updateProviderProfile(profile.id, {
        name: nextName,
        kind: profile.kind,
        provider_id: profile.provider_id || null,
        base_url: profile.base_url,
        model_id: profile.model_id,
        reasoning_effort: profile.reasoning_effort ?? null,
        context_window: profile.context_window ?? null,
        max_tokens: profile.max_tokens ?? null,
        api_key: null,
        is_default: profile.is_default,
      });
      await refresh(true);
    } catch {
      notify("显示名未能保存，请重试。");
    }
  };

  const startAuthorization = async (profileId: number) => {
    const operation = await authorizeProviderProfile(profileId, "oauth");
    setAuthProfileId(profileId);
    setAuthOperation(operation);
    setAuthPromptValue("");
    notify("请按 Provider 指引完成授权。");
  };

  const submitAuthPrompt = async () => {
    const prompt = authOperation?.prompts[0];
    if (!authOperation || !prompt || !authPromptValue.trim()) return;
    try {
      setAuthOperation(await submitProviderAuthInput(authOperation.id, prompt.prompt_id, authPromptValue));
      setAuthPromptValue("");
    } catch {
      notify("认证输入未被接受，请重试。");
    }
  };

  const cancelAuthorization = async () => {
    if (!authOperation) return;
    try {
      setAuthOperation(await cancelProviderAuthOperation(authOperation.id));
      notify("Provider 授权已取消。");
    } catch {
      notify("授权未能取消，请重试。");
    }
  };

  // OAuth 授权轮询：非终态时 900ms 后读取一次操作状态。
  useEffect(() => {
    if (!authOperation || isAuthTerminal(authOperation)) return;
    const timer = window.setTimeout(() => {
      void getProviderAuthOperation(authOperation.id)
        .then(async (next) => {
          setAuthOperation(next);
          if (next.status === "succeeded") {
            if (authProfileId !== null) {
              await takeProviderAuthResult(authProfileId, next.id);
            }
            notify("Provider 授权成功，可以测试连接。");
            await refresh(true);
          }
        })
        .catch(() => notify("认证状态暂时无法读取，可重试或取消。"));
    }, 900);
    return () => window.clearTimeout(timer);
  }, [authOperation, authProfileId, notify, refresh]);

  const selectedAuthModes = selectedProfile
    ? catalog?.providers.find((provider) => provider.provider_id === selectedProfile.provider_id)?.auth_modes
      ?? (isCustomProviderKind(selectedProfile.kind) ? ["api_key" as const] : [])
    : [];

  const wizardModelOptions = wizardCustom
    ? customModels.map((model) => ({ id: model.model_id, label: model.model_id }))
    : (wizardCatalogEntry?.models ?? []).map((model) => ({ id: model.model_id, label: model.model_name ?? model.model_id }));
  const wizardBasePreview = wizardCustom
    ? previewCustomRequestUrl(customKind, wizardDraft.baseUrl)
    : previewBuiltinRequestUrl(catalog, wizardDraft.typeId);

  const detail = selectedProfile;
  const lastKeeper = profiles.length <= 1;

  return (
    <Panel className="task6-provider-panel">
      {loading ? (
        <Loading>正在读取设置</Loading>
      ) : (
        <div className="task6-provider-master">
          <div aria-label="Provider 档案" className="task6-provider-list">
            {profiles.map((profile) => (
              <button
                aria-current={detail?.id === profile.id ? "true" : undefined}
                className="task6-provider-list-item"
                key={profile.id}
                onClick={() => { setSelectedId(profile.id); setKeyDraftOpen(false); setCredentialDraft(""); setLastTest(null); }}
                role="listitem"
                type="button"
              >
                <span aria-hidden="true" className="task6-provider-dot" data-ready={profile.status === "ready"} />
                <span className="task6-provider-list-text">
                  <span className="task6-provider-list-name">{profile.name}</span>
                  <span className="task6-provider-list-type">{providerTypeLabel(profile, catalog)}</span>
                </span>
                {profile.is_default ? <Badge tone="neutral">当前使用</Badge> : null}
              </button>
            ))}
            {profiles.length === 0 ? <p className="task6-muted">还没有 Provider 档案。</p> : null}
            <Button className="task6-provider-add" onClick={() => void openWizard()} variant="primary">
              + 添加服务
            </Button>
          </div>

          <div className="task6-provider-detail-pane">
            {!detail ? (
              <div className="task6-provider-empty">
                <p className="task6-muted">在左侧选择一个档案查看详情，或点「+ 添加服务」。</p>
              </div>
            ) : (
              <article className="task6-provider-detail" key={detail.id}>
                <div className="task6-provider-head">
                  {nameDraft === null ? (
                    <button
                      className="task6-provider-name-edit"
                      onClick={() => setNameDraft(detail.name)}
                      title="点击修改显示名"
                      type="button"
                    >
                      {detail.name}
                    </button>
                  ) : (
                    <input
                      aria-label="Provider 显示名"
                      autoFocus
                      className="task6-provider-name-input"
                      onBlur={() => void commitNameEdit(detail)}
                      onChange={(event) => setNameDraft(event.target.value)}
                      onKeyDown={(event) => {
                        if (event.key === "Enter") void commitNameEdit(detail);
                        if (event.key === "Escape") setNameDraft(null);
                      }}
                      value={nameDraft}
                    />
                  )}
                  {detail.is_default ? <Badge tone="neutral">当前使用</Badge> : <Button disabled={switchingProvider} onClick={() => void makeActive(detail.id)} size="compact" variant="secondary">设为当前</Button>}
                  <Status tone={providerStatusTone(detail.status)}>{providerStateLabel(detail.status)}</Status>
                </div>

                <dl className="task6-provider-conn">
                  <div className="task6-provider-conn-row">
                    <dt>类型</dt>
                    <dd>{providerTypeLabel(detail, catalog)}</dd>
                  </div>
                  <div className="task6-provider-conn-row">
                    <dt>Base URL</dt>
                    <dd>
                      <span className="task6-mono">{detail.base_url || "由服务商标定"}</span>
                      {isCustomProviderKind(detail.kind) && detail.base_url ? (
                        <span className="task6-provider-preview">
                          实际请求地址：<span className="task6-mono">{previewCustomRequestUrl(detail.kind, detail.base_url)}</span>
                          （带不带 /v1 都行，按服务商文档填写）
                        </span>
                      ) : null}
                    </dd>
                  </div>
                  <div className="task6-provider-conn-row">
                    <dt>API Key</dt>
                    <dd>
                      <span className="task6-provider-keyline">
                        <span className="task6-mono">{detail.credential_configured ? "•••• 已配置" : "未配置"}</span>
                        <span className="task6-inline-actions">
                          {selectedAuthModes.includes("api_key") ? (
                            <Button onClick={() => { setKeyDraftOpen((open) => !open); setCredentialDraft(""); }} size="compact" variant="ghost">
                              {keyDraftOpen ? "收起" : "更换"}
                            </Button>
                          ) : null}
                          {detail.credential_configured ? (
                            <Button
                              onClick={() => setConfirmAction({
                                title: "移除 Provider credential",
                                impact: "移除或撤销认证后 Coach 将不可用，本地分析不受影响。",
                                run: async () => { await deleteProviderCredential(detail.id); },
                              })}
                              size="compact"
                              variant="ghost"
                            >
                              移除
                            </Button>
                          ) : null}
                        </span>
                      </span>
                      {keyDraftOpen && selectedAuthModes.includes("api_key") ? (
                        <span className="task6-provider-keydraft">
                          <FieldControl
                            autoComplete="off"
                            onChange={(event) => setCredentialDraft(event.target.value)}
                            type={keyDraftVisible ? "text" : "password"}
                            value={credentialDraft}
                          />
                          <Button onClick={() => setKeyDraftVisible((open) => !open)} size="compact" variant="ghost">{keyDraftVisible ? "隐藏" : "显示"}</Button>
                          <Button
                            disabled={!credentialDraft}
                            onClick={() => {
                              const key = credentialDraft;
                              setConfirmAction({
                                title: "更换 Provider credential",
                                impact: "现有 credential 将被替换，Coach 连接可能需要重新测试。",
                                run: async () => {
                                  await setProviderApiKey(detail.id, key);
                                  setCredentialDraft("");
                                  setKeyDraftOpen(false);
                                },
                              });
                            }}
                            size="compact"
                            variant="secondary"
                          >
                            保存新 Key
                          </Button>
                        </span>
                      ) : null}
                      <span className="task6-provider-preview">密钥只存在本机，不会上传。</span>
                    </dd>
                  </div>
                  <div className="task6-provider-conn-row">
                    <dt>测连状态</dt>
                    <dd>
                      {detail.status === "ready" && !lastTest ? (
                        <span>上次测连成功{shortTime(detail.updated_at) ? ` · ${shortTime(detail.updated_at)}` : ""}</span>
                      ) : null}
                      {detail.status !== "ready" && !lastTest ? (
                        <span>上次测连未通过（{providerStateLabel(detail.status)}）</span>
                      ) : null}
                      {lastTest ? (
                        lastTest.passed ? <span className="task6-ok">{lastTest.message}</span> : <Notice tone="error">{lastTest.message}</Notice>
                      ) : null}
                      <span className="task6-inline-actions">
                        <Button disabled={testingConnection} onClick={() => void testConnection(detail)} size="compact" variant="secondary">
                          {testingConnection ? "正在测试…" : "测试连接"}
                        </Button>
                      </span>
                    </dd>
                  </div>
                  <div className="task6-provider-conn-row">
                    <dt>模型</dt>
                    <dd><span className="task6-mono">{detail.model_id || "未指定"}</span></dd>
                  </div>
                </dl>

                {selectedAuthModes.includes("oauth") ? (
                  <div className="task6-inline-actions">
                    <Button
                      onClick={() => setConfirmAction({
                        title: "开始 Provider 授权",
                        impact: "将打开 Provider 支持的 OAuth 或设备码授权流程。",
                        run: async () => { await startAuthorization(detail.id); },
                      })}
                      size="compact"
                      variant="secondary"
                    >
                      重新认证（OAuth / 设备码）
                    </Button>
                  </div>
                ) : null}

                <div className="task6-provider-danger">
                  <div>
                    <strong>删除此档案</strong>
                    <p className="task6-muted">
                      删除本地 Provider 配置与 credential，不会删除 Analysis。
                      {lastKeeper ? " 至少保留一个档案。" : detail.is_default ? " 它正在使用中，请先切换到其他档案。" : ""}
                    </p>
                  </div>
                  <Button
                    disabled={lastKeeper || detail.is_default}
                    onClick={() => setConfirmAction({
                      title: "删除 Provider",
                      impact: "删除此本地 Provider 配置与 credential，不会删除 Analysis。",
                      run: async () => {
                        await deleteProviderProfile(detail.id);
                        setSelectedId((current) => (current === detail.id ? null : current));
                      },
                    })}
                    size="compact"
                    variant="danger"
                  >
                    删除档案
                  </Button>
                </div>
              </article>
            )}

            {authOperation ? (
              <section aria-live="polite" className="task6-auth-operation">
                <div className="task6-auth-operation-head">
                  <span className="task6-auth-operation-title">Provider 授权</span>
                  <Status tone={authOperation.status === "succeeded" ? "success" : authOperation.status === "failed" || authOperation.status === "timed_out" ? "error" : "info"}>
                    {authOperationLabel(authOperation.status)}
                  </Status>
                </div>
                {authOperation.events.map((event, index) => (
                  <div key={`${event.type}-${index}`}>
                    {event.type === "auth_url" ? <a href={event.url} rel="noreferrer" target="_blank">打开 Provider 授权页</a> : null}
                    {event.type === "device_code" ? <p>设备码：<strong>{event.user_code}</strong> · <a href={event.verification_uri} rel="noreferrer" target="_blank">前往验证</a></p> : null}
                    {event.type === "progress" ? <p>{event.message}</p> : null}
                  </div>
                ))}
                {authOperation.prompts[0] ? (
                  <Field label={authOperation.prompts[0].message}>
                    <div className="task6-inline-actions">
                      {authOperation.prompts[0].type === "select" ? (
                        <select onChange={(event) => setAuthPromptValue(event.target.value)} value={authPromptValue}>
                          <option value="">请选择</option>
                          {authOperation.prompts[0].options?.map((option) => <option key={option.id} value={option.id}>{option.label}</option>)}
                        </select>
                      ) : <FieldControl autoComplete="off" onChange={(event) => setAuthPromptValue(event.target.value)} type={authOperation.prompts[0].type === "secret" ? "password" : "text"} value={authPromptValue} />}
                      <Button disabled={!authPromptValue.trim()} onClick={() => void submitAuthPrompt()} variant="secondary">提交</Button>
                    </div>
                  </Field>
                ) : null}
                {!isAuthTerminal(authOperation) ? <Button onClick={() => void cancelAuthorization()} variant="ghost">取消授权</Button> : null}
                {authOperation.error ? <Notice tone="error">{authOperation.error.message}</Notice> : null}
              </section>
            ) : null}
          </div>
        </div>
      )}

      <Dialog
        onClose={closeWizard}
        open={wizardOpen}
        title={`添加 Provider · 第 ${wizardStep}/4 步`}
        footer={
          createdProfile ? (
            <>
              <Button onClick={() => void makeActive(createdProfile.id).then(closeWizard)} variant="primary">设为当前并关闭</Button>
              <Button onClick={closeWizard} variant="secondary">仅保存</Button>
            </>
          ) : (
            <>
              {wizardStep > 1 ? <Button disabled={wizardCheckingNow} onClick={() => setWizardStep((step) => (step - 1) as WizardStep)} variant="secondary">上一步</Button> : <Button onClick={closeWizard} variant="secondary">取消</Button>}
              {wizardStep < 4 ? (
                <Button
                  disabled={!wizardStepReady(wizardStep, wizardDraft, Boolean(wizardCatalogEntry))}
                  onClick={() => setWizardStep((step) => (step + 1) as WizardStep)}
                  variant="primary"
                >
                  下一步
                </Button>
              ) : (
                <Button disabled={!wizardVerified} onClick={() => void finishWizard().catch(() => notify("Provider 未能添加，请检查输入后重试。"))} variant="primary">
                  完成
                </Button>
              )}
            </>
          )
        }
      >
        <ol className="task6-wizard-steps" aria-label="向导步骤">
          {WIZARD_STEP_LABELS.map((label, index) => (
            <li
              aria-current={wizardStep === index + 1 ? "step" : undefined}
              data-state={wizardStep > index + 1 ? "done" : wizardStep === index + 1 ? "current" : "todo"}
              key={label}
            >
              {index + 1}. {label}
            </li>
          ))}
        </ol>

        {createdProfile ? (
          <div aria-live="polite">
            <p>已添加「{createdProfile.name}」。</p>
            <p className="task6-muted">{createdProfile.is_default ? "它是第一个档案，已自动设为当前使用。" : "要让它成为 Coach 当前使用的 Provider 吗？"}</p>
          </div>
        ) : wizardStep === 1 ? (
          <div className="task6-wizard-type-grid" role="radiogroup" aria-label="Provider 类型">
            {WIZARD_TYPES.map((type) => {
              const available = type.custom || Boolean(wizardCatalogProvider(catalog, type.id));
              return (
                <label className="task6-mode-card" data-selected={wizardDraft.typeId === type.id} key={type.id}>
                  <input
                    checked={wizardDraft.typeId === type.id}
                    disabled={!available}
                    name="wizard-type"
                    onChange={() => wizardSetDraft({ typeId: type.id })}
                    type="radio"
                    value={type.id}
                  />
                  <span className="task6-mode-card-name">{type.label}</span>
                  <span className="task6-muted">{available ? type.hint : "当前目录中暂不可用"}</span>
                </label>
              );
            })}
          </div>
        ) : wizardStep === 2 ? (
          <div className="task6-wizard-step-body">
            <Field label="显示名称">
              <FieldControl
                onChange={(event) => wizardSetDraft({ name: event.target.value })}
                placeholder={wizardDefaultName(catalog, wizardDraft.typeId)}
                value={wizardDraft.name}
              />
            </Field>
            {wizardCustom ? (
              <Field label="Base URL" hint="带不带 /v1 都行，按服务商文档填写。">
                <FieldControl
                  autoComplete="off"
                  onChange={(event) => wizardSetDraft({ baseUrl: event.target.value })}
                  placeholder={customKind === "custom_anthropic_compatible" ? "https://provider.example" : "https://provider.example/v1"}
                  value={wizardDraft.baseUrl}
                />
              </Field>
            ) : (
              <p className="task6-muted">端点由服务商标定：<span className="task6-mono">{wizardBasePreview || "—"}</span></p>
            )}
            {wizardCustom && wizardBasePreview ? (
              <p className="task6-muted">实际请求地址：<span className="task6-mono">{wizardBasePreview}</span></p>
            ) : null}
            {wizardCustom && customModelMessage ? <p className="task6-muted" aria-live="polite">{customModelMessage}</p> : null}
          </div>
        ) : wizardStep === 3 ? (
          <div className="task6-wizard-step-body">
            <Field label="API Key">
              <span className="task6-provider-keydraft">
                <FieldControl
                  autoComplete="off"
                  onChange={(event) => wizardSetDraft({ apiKey: event.target.value })}
                  type={wizardShowKey ? "text" : "password"}
                  value={wizardDraft.apiKey}
                />
                <Button onClick={() => setWizardShowKey((open) => !open)} size="compact" variant="ghost">{wizardShowKey ? "隐藏" : "显示"}</Button>
              </span>
            </Field>
            <p className="task6-muted">密钥只存在本机，不会上传；此步尚未写入档案。</p>
            {wizardCustom ? (
              <>
                {customModelState === "loading" ? <p className="task6-muted" aria-live="polite">正在读取可用模型…</p> : null}
                {customModelState === "loaded" ? (
                  <Field label="Model">
                    <select className="ac-field__control" onChange={(event) => wizardSetDraft({ modelId: event.target.value })} value={wizardDraft.modelId}>
                      <option value="">选择 Model</option>
                      {customModels.map((model) => <option key={model.model_id} value={model.model_id}>{model.model_id}</option>)}
                    </select>
                    <Button onClick={() => { wizardSetDraft({ modelId: "" }); customDiscovery.enterManualMode(); }} size="compact" variant="ghost">列表中没有需要的 Model ID</Button>
                  </Field>
                ) : null}
                {customModelState === "manual" ? (
                  <Field label="Model ID">
                    <FieldControl autoComplete="off" onChange={(event) => wizardSetDraft({ modelId: event.target.value })} value={wizardDraft.modelId} />
                  </Field>
                ) : null}
              </>
            ) : (
              <Field label="Model">
                <select className="ac-field__control" onChange={(event) => wizardSetDraft({ modelId: event.target.value })} value={wizardDraft.modelId}>
                  <option value="">选择 Model</option>
                  {wizardModelOptions.map((model) => <option key={model.id} value={model.id}>{model.label}</option>)}
                </select>
              </Field>
            )}
            {wizardModelIsReasoning ? (
              <Field label="思考力度">
                <select
                  className="ac-field__control"
                  onChange={(event) => wizardSetDraft({ reasoningEffort: event.target.value as ProviderReasoningEffort | "" })}
                  value={wizardDraft.reasoningEffort}
                >
                  <option value="">默认（推理模型回落高档）</option>
                  <option value="off">关闭</option>
                  <option value="minimal">极简</option>
                  <option value="low">低</option>
                  <option value="medium">中</option>
                  <option value="high">高</option>
                </select>
              </Field>
            ) : null}
          </div>
        ) : (
          <div aria-live="polite" className="task6-wizard-step-body">
            {wizardCheck.phase === "idle" ? <p className="task6-muted">点「测试连接」验证这份配置；通过后才能完成添加。</p> : null}
            {wizardCheckingNow ? <p className="task6-muted">正在测试连接…再次点击「停止检查」可取消。</p> : null}
            {wizardCheck.phase === "done" && wizardCheck.fingerprint === wizardFingerprint ? (
              wizardCheck.passed
                ? <p className="task6-ok">{wizardCheck.message}（可点「完成」保存）</p>
                : <Notice tone="error">{wizardCheck.message}</Notice>
            ) : null}
            <Button disabled={!wizardPayload} onClick={() => void runWizardCheck()} variant="secondary">
              {wizardCheckingNow ? "停止检查" : "测试连接"}
            </Button>
            {wizardCustom && !customProtocolConfirmed && wizardDraft.apiKey && wizardDraft.baseUrl ? (
              <p className="task6-muted">无法自动识别接口协议时，将回退为手动填写 Model ID；可回到第 2 步调整端点后重试。</p>
            ) : null}
          </div>
        )}
      </Dialog>

      <Dialog
        footer={<><Button onClick={() => setConfirmAction(null)} variant="secondary">取消</Button><Button onClick={() => confirmAction?.run().then(() => { setConfirmAction(null); void refresh(true); }).catch(() => notify("操作未完成，未伪造成功状态，请重试。"))} variant="danger">确认</Button></>}
        onClose={() => setConfirmAction(null)}
        open={Boolean(confirmAction)}
        title={confirmAction?.title ?? "确认操作"}
      >
        <p>{confirmAction?.impact}</p>
      </Dialog>
    </Panel>
  );
}
