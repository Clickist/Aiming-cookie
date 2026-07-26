"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import {
  authorizeProviderProfile,
  cancelProviderAuthOperation,
  createProviderProfile,
  deleteCalibrationProfile,
  deleteProviderCredential,
  deleteProviderProfile,
  getCalibrationProfile,
  getCaptureStatus,
  getProviderAuthOperation,
  getProviderCatalog,
  getStorage,
  listIncompleteCaptures,
  listKovaakRuns,
  listProviderProfiles,
  removeIncompleteCapture,
  removeRunEvidence,
  saveCalibrationProfile,
  setDefaultProviderProfile,
  setProviderApiKey,
  submitProviderAuthInput,
  testProviderProfile,
} from "@/lib/api";
import { presentStorageCategories } from "@/lib/contracts";
import { isDesktopRuntime, setDesktopCaptureEnabled } from "@/lib/desktop";
import type {
  CalibrationProfileV1,
  CaptureStatusV1,
  IncompleteCaptureItemV1,
  KovaaKRunListItem,
  ProviderAuthMode,
  ProviderAuthOperation,
  ProviderCatalogV1,
  ProviderProfile,
  ProviderProfileState,
  StorageResponse,
} from "@/lib/types";
import {
  Badge,
  Button,
  Dialog,
  ErrorState,
  Field,
  FieldControl,
  Loading,
  Notice,
  Panel,
  Status,
  Toast,
} from "@/ui/primitives";
import { useTheme } from "@/ui/theme";

type ConfirmAction = {
  title: string;
  impact: string;
  run: () => Promise<void>;
} | null;

function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / 1024 ** index).toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
}

function captureLabel(value: boolean | null | undefined, yes: string, no: string): string {
  if (value == null) return "未知";
  return value ? yes : no;
}

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

function authOperationLabel(status: ProviderAuthOperation["status"]): string {
  switch (status) {
    case "running": return "正在连接 Provider";
    case "awaiting_input": return "等待认证输入";
    case "succeeded": return "授权成功";
    case "failed": return "授权失败";
    case "cancelled": return "已取消";
    case "timed_out": return "已超时";
    case "interrupted": return "授权已中断";
  }
}

function rawPermissionLabel(value: CaptureStatusV1["raw_input_permission"]): string {
  return value === "granted" ? "已允许" : value === "denied" ? "已拒绝" : "尚未决定";
}

function runtimeHealthLabel(value: CaptureStatusV1["runtime_health"]): string {
  return value === "healthy" ? "正常" : value === "degraded" ? "部分能力受限" : "不可用";
}

function finalizationLabel(value: string): string {
  const labels: Record<string, string> = {
    idle: "待命",
    discovered: "已发现训练",
    capturing: "采集中",
    pending: "等待整理",
    finalizing: "整理中",
    finalized: "已完成",
    failed: "整理失败",
    unknown: "状态未知",
  };
  return labels[value] ?? "状态未知";
}

function incompleteReasonLabel(value: IncompleteCaptureItemV1["reason"]): string {
  return value === "interrupted_finalization" ? "整理过程被中断" : "未归类的采集产物";
}

function firstAuthMode(modes: ProviderAuthMode[] | undefined): ProviderAuthMode {
  if (modes?.includes("api_key")) return "api_key";
  return modes?.[0] ?? "api_key";
}

function isAuthTerminal(operation: ProviderAuthOperation): boolean {
  return ["succeeded", "failed", "cancelled", "timed_out", "interrupted"].includes(operation.status);
}

export function SettingsWorkspace() {
  const { preference, setPreference } = useTheme();
  const [profiles, setProfiles] = useState<ProviderProfile[]>([]);
  const [catalog, setCatalog] = useState<ProviderCatalogV1 | null>(null);
  const [calibration, setCalibration] = useState<CalibrationProfileV1 | null>(null);
  const [capture, setCapture] = useState<CaptureStatusV1 | null>(null);
  const [storage, setStorage] = useState<StorageResponse | null>(null);
  const [incomplete, setIncomplete] = useState<IncompleteCaptureItemV1[]>([]);
  const [runs, setRuns] = useState<KovaaKRunListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [confirmAction, setConfirmAction] = useState<ConfirmAction>(null);
  const [profileName, setProfileName] = useState("");
  const [providerId, setProviderId] = useState("");
  const [modelId, setModelId] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [newApiKey, setNewApiKey] = useState("");
  const [newAuthMode, setNewAuthMode] = useState<ProviderAuthMode>("api_key");
  const [credentialDrafts, setCredentialDrafts] = useState<Record<number, string>>({});
  const [authOperation, setAuthOperation] = useState<ProviderAuthOperation | null>(null);
  const [authPromptValue, setAuthPromptValue] = useState("");
  const [cmPer360, setCmPer360] = useState("");
  const [fov, setFov] = useState("");

  const desktop = isDesktopRuntime();

  const refresh = useCallback(async () => {
    try {
      const [profileResult, catalogResult, calibrationResult] = await Promise.all([
        listProviderProfiles(),
        getProviderCatalog(),
        getCalibrationProfile(),
      ]);
      setProfiles(profileResult.profiles);
      setCatalog(catalogResult);
      setCalibration(calibrationResult);
      setCmPer360(calibrationResult.values.cm_per_360?.toString() ?? "");
      setFov(calibrationResult.values.fov?.toString() ?? "");
      if (desktop) {
        const [captureResult, storageResult, incompleteResult, runResult] = await Promise.all([
          getCaptureStatus(),
          getStorage(),
          listIncompleteCaptures(),
          listKovaakRuns(),
        ]);
        setCapture(captureResult);
        setStorage(storageResult);
        setIncomplete(incompleteResult.items);
        setRuns(runResult.runs);
      }
      setLoadError(false);
    } catch {
      setLoadError(true);
    } finally {
      setLoading(false);
    }
  }, [desktop]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const selectedCatalogProvider = useMemo(
    () => catalog?.providers.find((provider) => provider.provider_id === providerId)
      ?? (providerId ? undefined : catalog?.providers[0]),
    [catalog, providerId],
  );

  useEffect(() => {
    if (providerId === "custom") return;
    setModelId(selectedCatalogProvider?.models[0]?.model_id ?? "");
    setNewAuthMode(firstAuthMode(selectedCatalogProvider?.auth_modes));
  }, [providerId, selectedCatalogProvider]);

  useEffect(() => {
    if (!authOperation || isAuthTerminal(authOperation)) return;
    const timer = window.setTimeout(() => {
      void getProviderAuthOperation(authOperation.operation_id)
        .then(async (next) => {
          setAuthOperation(next);
          if (next.status === "succeeded") {
            setFeedback("Provider 授权成功，可以测试连接。");
            await refresh();
          }
        })
        .catch(() => setFeedback("认证状态暂时无法读取，可重试或取消。"));
    }, 900);
    return () => window.clearTimeout(timer);
  }, [authOperation, refresh]);

  const addProfile = async () => {
    const custom = providerId === "custom";
    const created = await createProviderProfile({
      name: profileName.trim() || (custom ? "自定义 Provider" : selectedCatalogProvider?.provider_name ?? "Provider"),
      kind: custom ? "custom_openai_compatible" : "builtin",
      provider_id: custom ? null : selectedCatalogProvider?.provider_id,
      base_url: custom ? baseUrl.trim() : null,
      model_id: modelId.trim(),
      api_key: custom || newAuthMode === "api_key" ? newApiKey : null,
      is_default: profiles.length === 0,
    });
    setNewApiKey("");
    setFeedback(`已添加 ${created.name}`);
    await refresh();
  };

  const startAuthorization = async (profileId: number) => {
    const operation = await authorizeProviderProfile(profileId, "oauth");
    setAuthOperation(operation);
    setAuthPromptValue("");
    setFeedback("请按 Provider 指引完成授权。");
  };

  const submitAuthPrompt = async () => {
    const prompt = authOperation?.prompts[0];
    if (!authOperation || !prompt || !authPromptValue.trim()) return;
    try {
      setAuthOperation(await submitProviderAuthInput(
        authOperation.operation_id,
        prompt.prompt_id,
        authPromptValue,
      ));
      setAuthPromptValue("");
    } catch {
      setFeedback("认证输入未被接受，请重试。");
    }
  };

  const cancelAuthorization = async () => {
    if (!authOperation) return;
    try {
      setAuthOperation(await cancelProviderAuthOperation(authOperation.operation_id));
      setFeedback("Provider 授权已取消。");
    } catch {
      setFeedback("授权未能取消，请重试。");
    }
  };

  const saveProfileCalibration = async () => {
    const result = await saveCalibrationProfile({
      cm_per_360: cmPer360 ? Number(cmPer360) : null,
      fov: fov ? Number(fov) : null,
    });
    setCalibration(result);
    setFeedback("配置档默认值已保存");
  };

  const ask = (title: string, impact: string, run: () => Promise<void>) => {
    setConfirmAction({ title, impact, run });
  };

  const confirm = async () => {
    if (!confirmAction) return;
    try {
      await confirmAction.run();
      setConfirmAction(null);
      await refresh();
    } catch {
      setFeedback("操作未完成，未伪造成功状态，请重试。");
    }
  };

  const customProvider = providerId === "custom";
  const canAddProvider = customProvider
    ? Boolean(baseUrl.trim() && modelId.trim() && newApiKey.trim())
    : Boolean(selectedCatalogProvider && modelId.trim() && (newAuthMode !== "api_key" || newApiKey.trim()));

  if (loading) return <div className="task6-settings-page"><Loading>正在读取设置</Loading></div>;
  if (loadError && !catalog && profiles.length === 0) {
    return <div className="task6-settings-page"><ErrorState title="设置暂时不可用"><Button onClick={() => void refresh()} variant="secondary">重试</Button></ErrorState></div>;
  }

  return (
    <div className="task6-settings-page">
      <header className="task6-page-header">
        <div><span className="task6-eyebrow">本地应用设置</span><h1>设置</h1></div>
        <Button onClick={() => void refresh()} variant="ghost">刷新状态</Button>
      </header>

      {loadError ? <Notice tone="warning" title="部分设置未能刷新">已保留当前可用内容。请检查本地服务后重试。</Notice> : null}

      <div className="task6-settings-grid">
        <Panel className="task6-settings-section" title={<><span>Provider</span><Badge>{profiles.length}</Badge></>}>
          <p className="task6-muted">密钥只会提交到本地 credential store；界面不会回显已有密钥。</p>
          {profiles.map((profile) => {
            const authModes = catalog?.providers.find((provider) => provider.provider_id === profile.provider_id)?.auth_modes
              ?? (profile.kind === "custom_openai_compatible" ? ["api_key" as const] : []);
            return (
              <article className="task6-provider-row" key={profile.id}>
                <div>
                  <div className="task6-row-title"><strong>{profile.name}</strong>{profile.is_default ? <Badge tone="info">默认</Badge> : null}</div>
                  <p>{profile.provider_id} · {profile.model_id}</p>
                  <Status tone={profile.status === "ready" ? "success" : profile.status === "needs_reauth" || profile.status === "auth_expired" ? "warning" : profile.status === "connection_failed" || profile.status === "model_unavailable" ? "error" : "neutral"}>{providerStateLabel(profile.status)}</Status>
                </div>
                <div className="task6-provider-actions">
                  <Button onClick={() => void testProviderProfile(profile.id).then((status) => setFeedback(status.message)).catch(() => setFeedback("连接测试失败，请检查 Provider 与网络。"))} size="compact" variant="ghost">测试连接</Button>
                  {!profile.is_default ? <Button onClick={() => void setDefaultProviderProfile(profile.id).then(() => refresh()).catch(() => setFeedback("默认 Provider 未能更新。"))} size="compact" variant="ghost">设为默认</Button> : null}
                  {authModes.includes("oauth") ? <Button onClick={() => ask("开始 Provider 授权", "将打开 Provider 支持的 OAuth 或设备码授权流程。", () => startAuthorization(profile.id))} size="compact" variant="ghost">OAuth / 设备码</Button> : null}
                  {authModes.includes("api_key") ? (
                    <>
                      <Field label="更换 API key" hint="仅本次提交保存在内存，提交后立即清空。">
                        <FieldControl autoComplete="off" onChange={(event) => setCredentialDrafts((current) => ({ ...current, [profile.id]: event.target.value }))} type="password" value={credentialDrafts[profile.id] ?? ""} />
                      </Field>
                      <Button disabled={!credentialDrafts[profile.id]} onClick={() => ask("更换 Provider credential", "现有 credential 将被替换，Coach 连接可能需要重新测试。", async () => { await setProviderApiKey(profile.id, credentialDrafts[profile.id] ?? ""); setCredentialDrafts((current) => ({ ...current, [profile.id]: "" })); })} size="compact" variant="secondary">更换</Button>
                    </>
                  ) : null}
                  {profile.credential_configured ? <Button onClick={() => ask("移除 Provider credential", "移除或撤销认证后 Coach 将不可用，本地分析不受影响。", async () => { await deleteProviderCredential(profile.id); })} size="compact" variant="ghost">移除认证</Button> : null}
                  <Button onClick={() => ask("删除 Provider", "删除此本地 Provider 配置与 credential，不会删除 Analysis。", async () => { await deleteProviderProfile(profile.id); })} size="compact" variant="danger">删除</Button>
                </div>
              </article>
            );
          })}
          {authOperation ? (
            <section aria-live="polite" className="task6-auth-operation">
              <div className="task6-row-title"><strong>Provider 授权</strong><Status tone={authOperation.status === "succeeded" ? "success" : authOperation.status === "failed" || authOperation.status === "timed_out" || authOperation.status === "interrupted" ? "error" : "info"}>{authOperationLabel(authOperation.status)}</Status></div>
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
          <div className="task6-provider-form">
            <h3>添加 Provider</h3>
            <Field label="类型">
              <select onChange={(event) => setProviderId(event.target.value)} value={providerId || selectedCatalogProvider?.provider_id || ""}>
                {catalog?.providers.map((provider) => <option key={provider.provider_id} value={provider.provider_id}>{provider.provider_name}</option>)}
                <option value="custom">自定义 OpenAI-compatible</option>
              </select>
            </Field>
            <Field label="显示名称"><FieldControl onChange={(event) => setProfileName(event.target.value)} value={profileName} /></Field>
            {customProvider ? <Field label="Base URL"><FieldControl onChange={(event) => setBaseUrl(event.target.value)} placeholder="https://provider.example/v1" value={baseUrl} /></Field> : null}
            {customProvider ? <Field label="Model ID"><FieldControl onChange={(event) => setModelId(event.target.value)} value={modelId} /></Field> : (
              <Field label="Model">
                <select onChange={(event) => setModelId(event.target.value)} value={modelId}>
                  {selectedCatalogProvider?.models.map((model) => <option key={model.model_id} value={model.model_id}>{model.model_name ?? model.model_id}</option>)}
                </select>
              </Field>
            )}
            {!customProvider && selectedCatalogProvider ? (
              <Field label="认证方式">
                <select onChange={(event) => setNewAuthMode(event.target.value as ProviderAuthMode)} value={newAuthMode}>
                  {selectedCatalogProvider.auth_modes.map((mode) => <option key={mode} value={mode}>{mode === "api_key" ? "API Key" : mode === "oauth" ? "OAuth / 设备码" : "环境凭据"}</option>)}
                </select>
              </Field>
            ) : null}
            {customProvider || newAuthMode === "api_key" ? <Field label="API key"><FieldControl autoComplete="off" onChange={(event) => setNewApiKey(event.target.value)} type="password" value={newApiKey} /></Field> : null}
            <Button disabled={!canAddProvider} onClick={() => void addProfile().catch(() => setFeedback("Provider 未能添加，请检查输入后重试。"))}>添加 Provider</Button>
          </div>
        </Panel>

        <Panel className="task6-settings-section" title="配置档">
          <Notice title="自动读取优先">Stats 自动读取优先，其次是本局手动覆盖，再使用这里的配置档默认值；无法确定时不会猜值。</Notice>
          <div className="task6-field-grid">
            <Field label="cm/360" hint="仅作为 profile_default"><FieldControl inputMode="decimal" min="0.01" onChange={(event) => setCmPer360(event.target.value)} step="any" type="number" value={cmPer360} /></Field>
            <Field label="FOV" hint="仅作为 profile_default"><FieldControl inputMode="decimal" max="180" min="0.01" onChange={(event) => setFov(event.target.value)} step="any" type="number" value={fov} /></Field>
          </div>
          <p className="task6-muted">DPI：{calibration?.dpi ?? "待读取"} · Sensitivity：{calibration?.sensitivity ?? "待读取"}</p>
          <div className="task6-inline-actions"><Button disabled={!cmPer360 && !fov} onClick={() => void saveProfileCalibration().catch(() => setFeedback("配置档未能保存，请检查数值。"))}>保存配置档</Button><Button onClick={() => ask("删除配置档默认值", "之后仍会优先使用 Stats 或本局手动覆盖。", async () => { await deleteCalibrationProfile(); })} variant="ghost">删除</Button></div>
        </Panel>

        <Panel className="task6-settings-section" title="自动采集 / Raw Input">
          {!desktop ? <Notice tone="warning" title="浏览器模式">自动采集、Raw Input、硬件回放缓冲和权限管理仅在 Desktop 可用。</Notice> : null}
          {capture?.availability === "unavailable" ? <Notice tone="error" title="采集状态不可用">{capture.error?.message ?? "本地采集服务暂时不可用。"}</Notice> : null}
          {capture ? (
            <dl className="task6-status-grid">
              <div><dt>平台</dt><dd>{captureLabel(capture.platform_supported, "支持", "不支持")}</dd></div>
              <div><dt>Raw Input 权限</dt><dd>{rawPermissionLabel(capture.raw_input_permission)}</dd></div>
              <div><dt>采集</dt><dd>{captureLabel(capture.capture_enabled, "已启用", "已关闭")}</dd></div>
              <div><dt>KovaaK 进程</dt><dd>{captureLabel(capture.kovaak_process_present, "运行中", "未运行")}</dd></div>
              <div><dt>300 秒硬件缓冲</dt><dd>{captureLabel(capture.replay_buffer_active, "活动", "未活动")}</dd></div>
              <div><dt>Runtime</dt><dd>{runtimeHealthLabel(capture.runtime_health)}</dd></div>
              <div><dt>训练整理</dt><dd>{finalizationLabel(capture.finalization_state)}</dd></div>
              <div><dt>暂停局</dt><dd>{capture.pause_fail_closed ? "不生成永久结果" : capture.pause_state === "clear" ? "未检测到" : "状态未知"}</dd></div>
            </dl>
          ) : null}
          <p className="task6-muted">如需关闭 Raw Input 授权，请在 Windows 设置的隐私与应用权限中关闭本应用权限；关闭自动采集不会删除历史 trace。</p>
          {desktop && capture?.capture_enabled != null ? <Button onClick={() => void setDesktopCaptureEnabled(!capture.capture_enabled).then(() => refresh())} variant="secondary">{capture.capture_enabled ? "关闭未来采集" : "启用自动采集"}</Button> : null}
        </Panel>

        <Panel className="task6-settings-section" title="主题">
          <fieldset className="task6-theme-options">
            <legend>界面外观</legend>
            {(["system", "light", "dark"] as const).map((mode) => (
              <label key={mode}><input checked={preference === mode} name="theme" onChange={() => setPreference(mode)} type="radio" />{mode === "system" ? "跟随系统" : mode === "light" ? "浅色" : "深色"}</label>
            ))}
          </fieldset>
        </Panel>

        <Panel className="task6-settings-section task6-storage" title="存储">
          {!desktop ? <Notice tone="warning" title="Desktop 能力不可用">浏览器不会伪造本地占用或删除操作。</Notice> : null}
          {storage ? (
            <>
              <div className="task6-storage-total"><span>总占用</span><strong>{formatBytes(storage.total_bytes)}</strong></div>
              <div className="task6-storage-categories">
                {presentStorageCategories(storage.categories).map(([label, bytes]) => <div key={label}><span>{label}</span><strong>{formatBytes(bytes)}</strong></div>)}
              </div>
            </>
          ) : null}
          <Notice title="保留规则">不提供自动清理、自动 TTL、删除最旧或一键清空。用户源 Stats / Performance 不会被应用删除。</Notice>
          {runs.filter((run) => run.video_artifact_ref || run.trace_quality.state === "attached").map((run) => (
            <article className="task6-storage-row" key={run.run_ref}>
              <div><strong>{run.scenario ?? "未知场景"}</strong><p>{run.video_artifact_ref && run.trace_quality.state === "attached" ? "录像与 Raw trace" : run.video_artifact_ref ? "Run 录像" : "Raw trace"}</p></div>
              <div className="task6-inline-actions">
                {run.video_artifact_ref ? <Button onClick={() => ask("移除 Run 录像", "录像引用将变为 unavailable；Run metadata、Analysis 与用户源文件保留。", async () => { await removeRunEvidence(run.id, "video"); })} size="compact" variant="danger">移除录像</Button> : null}
                {run.trace_quality.state === "attached" ? <Button onClick={() => ask("移除 Raw trace", "依赖 Raw 的证据引用将变为 unavailable；Run metadata 与用户源文件保留。", async () => { await removeRunEvidence(run.id, "raw"); })} size="compact" variant="danger">移除 Raw</Button> : null}
              </div>
            </article>
          ))}
          {incomplete.map((item) => (
            <article className="task6-storage-row" key={item.item_ref}>
              <div><strong>未完成采集</strong><p>{formatBytes(item.size_bytes)} · {incompleteReasonLabel(item.reason)}</p></div>
              <Button disabled={!item.removable} onClick={() => ask("移除未完成采集", item.impact.message, async () => { await removeIncompleteCapture(item.item_ref); })} size="compact" variant="danger">移除</Button>
            </article>
          ))}
        </Panel>
      </div>

      <Dialog
        footer={<><Button onClick={() => setConfirmAction(null)} variant="secondary">取消</Button><Button onClick={() => void confirm()} variant="danger">确认</Button></>}
        onClose={() => setConfirmAction(null)}
        open={Boolean(confirmAction)}
        title={confirmAction?.title ?? "确认操作"}
      >
        <p>{confirmAction?.impact}</p>
      </Dialog>
      {feedback ? <Toast onClick={() => setFeedback(null)}>{feedback}</Toast> : null}
    </div>
  );
}
