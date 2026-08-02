"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import {
  authorizeProviderProfile,
  cancelProviderAuthOperation,
  createProviderProfile,
  discoverCustomProviderModels,
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
import { KovaaKConnectionPanel } from "@/components/kovaak/KovaaKConnectionPanel";
import type {
  CalibrationProfileV1,
  CaptureStatusV1,
  CustomProviderKind,
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

const STORAGE_COLORS = [
  "var(--on-surface)",
  "var(--outline)",
  "var(--on-surface-variant)",
  "var(--outline-variant)",
];

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

function isCustomProviderKind(kind: string): kind is CustomProviderKind {
  return kind === "custom_openai_compatible" || kind === "custom_anthropic_compatible";
}

function providerStatusTone(status: ProviderProfileState): "success" | "warning" | "error" | "neutral" {
  if (status === "ready") return "success";
  if (status === "needs_reauth" || status === "auth_expired") return "warning";
  if (status === "connection_failed" || status === "model_unavailable") return "error";
  return "neutral";
}

const NAV_ITEMS = [
  { id: "llm-provider", label: "LLM Provider" },
  { id: "profile", label: "Profile" },
  { id: "theme", label: "主题" },
  { id: "capture", label: "自动采集与 Raw Input" },
  { id: "kovaak", label: "KovaaK 成绩" },
  { id: "storage", label: "存储" },
];

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
  const [customKind, setCustomKind] = useState<CustomProviderKind>("custom_openai_compatible");
  const [customModels, setCustomModels] = useState<string[]>([]);
  const [customModelState, setCustomModelState] = useState<"idle" | "loading" | "loaded" | "manual">("idle");
  const [customModelMessage, setCustomModelMessage] = useState("");
  const [customProtocolNeedsChoice, setCustomProtocolNeedsChoice] = useState(false);
  const [customProtocolConfirmed, setCustomProtocolConfirmed] = useState(false);
  const [newApiKey, setNewApiKey] = useState("");
  const [newAuthMode, setNewAuthMode] = useState<ProviderAuthMode>("api_key");
  const [credentialDrafts, setCredentialDrafts] = useState<Record<number, string>>({});
  const [authOperation, setAuthOperation] = useState<ProviderAuthOperation | null>(null);
  const [authPromptValue, setAuthPromptValue] = useState("");
  const [cmPer360, setCmPer360] = useState("");
  const [fov, setFov] = useState("");
  const [expandedProviders, setExpandedProviders] = useState<Record<number, boolean>>({});
  const [activeNav, setActiveNav] = useState(NAV_ITEMS[0].id);

  const desktop = isDesktopRuntime();

  const refresh = useCallback(async () => {
    try {
      const [profileResult, catalogResult, calibrationResult] = await Promise.all([
        listProviderProfiles(),
        getProviderCatalog().catch(() => null),
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
      setLoadError(catalogResult === null);
    } catch {
      setLoadError(true);
    } finally {
      setLoading(false);
    }
  }, [desktop]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    const syncActiveNav = () => {
      const hash = window.location.hash.slice(1);
      setActiveNav(NAV_ITEMS.some((item) => item.id === hash) ? hash : NAV_ITEMS[0].id);
    };

    syncActiveNav();
    window.addEventListener("hashchange", syncActiveNav);
    return () => window.removeEventListener("hashchange", syncActiveNav);
  }, []);

  const selectedCatalogProvider = useMemo(
    () => catalog?.providers.find((provider) => provider.provider_id === providerId)
      ?? (providerId ? undefined : catalog?.providers[0]),
    [catalog, providerId],
  );

  useEffect(() => {
    if (providerId === "custom") return;
    setModelId("");
    setNewAuthMode(firstAuthMode(selectedCatalogProvider?.auth_modes));
  }, [providerId, selectedCatalogProvider]);

  useEffect(() => {
    if (providerId !== "custom" || customProtocolNeedsChoice || !baseUrl.trim() || !newApiKey.trim()) return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      setCustomModelState("loading");
      setModelId("");
      setCustomModelMessage("");
      void discoverCustomProviderModels({
        base_url: baseUrl.trim(),
        api_key: newApiKey,
      }, { signal: controller.signal })
        .then((response) => {
          if (controller.signal.aborted) return;
          setCustomKind(response.protocol === "anthropic-messages" ? "custom_anthropic_compatible" : "custom_openai_compatible");
          setCustomProtocolConfirmed(true);
          setCustomModels(response.models);
          if (response.models.length) {
            setCustomModelState("loaded");
          } else {
            setCustomModelState("manual");
            setCustomModelMessage("这个 Provider 没有返回可选 Model ID，请手动填写。");
          }
        })
        .catch(() => {
          if (controller.signal.aborted) return;
          setCustomModels([]);
          setCustomProtocolNeedsChoice(true);
          setCustomProtocolConfirmed(false);
          setCustomModelState("manual");
          setCustomModelMessage("无法自动识别接口协议或读取模型列表，请选择协议后手动填写 Model ID。");
        });
    }, 500);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [baseUrl, newApiKey, providerId, customProtocolNeedsChoice]);

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
      kind: custom ? customKind : "builtin",
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

  const resetCustomModels = () => {
    setModelId("");
    setCustomModels([]);
    setCustomModelState("idle");
    setCustomModelMessage("");
    setCustomProtocolNeedsChoice(false);
    setCustomProtocolConfirmed(false);
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
    ? Boolean(baseUrl.trim() && modelId.trim() && newApiKey.trim() && customProtocolConfirmed)
    : Boolean(selectedCatalogProvider && modelId.trim() && (newAuthMode !== "api_key" || newApiKey.trim()));

  const storageCategories = storage ? presentStorageCategories(storage.categories) : [];
  const totalBytes = storage?.total_bytes ?? 0;
  const storageBar = totalBytes > 0
    ? storageCategories.map(([, bytes]) => Math.max(0, bytes / totalBytes * 100))
    : [];

  const themeOptions = [
    { value: "system", label: "跟随系统" },
    { value: "light", label: "浅色" },
    { value: "dark", label: "深色" },
  ] as const;

  if (loading) return <div className="task6-settings-page"><Loading>正在读取设置</Loading></div>;
  if (loadError && !catalog && profiles.length === 0) {
    return <div className="task6-settings-page"><ErrorState title="设置暂时不可用"><Button onClick={() => void refresh()} variant="secondary">重试</Button></ErrorState></div>;
  }

  return (
    <div className="task6-settings-page">
      <div className="task6-settings-layout">
        <nav className="task6-settings-nav" aria-label="设置分区">
          <div className="task6-settings-nav-title">设置</div>
          {NAV_ITEMS.map((item) => (
            <a
              aria-current={item.id === activeNav ? "true" : undefined}
              className={["task6-settings-nav-link", item.id === activeNav ? "task6-active" : ""].filter(Boolean).join(" ")}
              href={`#${item.id}`}
              key={item.id}
            >
              {item.label}
            </a>
          ))}
        </nav>

        <div className="task6-settings-content">
          {loadError ? <Notice className="task6-settings-notice" tone="warning" title="部分设置未能刷新">已保留当前可用内容。请检查本地服务后重试。</Notice> : null}

          <section className="task6-settings-section" id="llm-provider">
            <div className="task6-settings-section-header">
              <span className="task6-settings-section-title">LLM Provider</span>
              <span className="task6-settings-section-hint">认证只发生在你与 Provider 之间</span>
            </div>
            <Panel className="task6-provider-panel">
              <p className="task6-muted">密钥只会提交到本地 credential store；界面不会回显已有密钥。</p>
              {profiles.map((profile) => {
                const authModes = catalog?.providers.find((provider) => provider.provider_id === profile.provider_id)?.auth_modes
                  ?? (isCustomProviderKind(profile.kind) ? ["api_key" as const] : []);
                const expanded = expandedProviders[profile.id] ?? false;
                return (
                  <article className="task6-provider-row" key={profile.id}>
                    <div className="task6-provider-main">
                      <div className="task6-provider-head">
                        <span className="task6-provider-name">{profile.name}</span>
                        <Status tone={providerStatusTone(profile.status)}>{providerStateLabel(profile.status)}</Status>
                        {profile.is_default ? <Badge tone="info">默认</Badge> : null}
                        <span className="task6-provider-actions" style={{ marginLeft: "auto" }}>
                          <Button
                            onClick={() => void testProviderProfile(profile.id).then((status) => setFeedback(status.message)).catch(() => setFeedback("连接测试失败，请检查 Provider 与网络。"))}
                            size="compact"
                            variant="ghost"
                          >
                            测试连接
                          </Button>
                          <Button
                            onClick={() => setExpandedProviders((current) => ({ ...current, [profile.id]: !current[profile.id] }))}
                            size="compact"
                            variant="ghost"
                          >
                            ⋯
                          </Button>
                        </span>
                      </div>
                      <p className="task6-provider-meta">
                        {profile.model_id ?? "未指定模型"}
                        {profile.provider_id ? ` · ${profile.provider_id}` : null}
                        {profile.status === "ready" ? " · 上次测试：可用" : null}
                      </p>
                      {expanded ? (
                        <div className="task6-provider-actions" style={{ marginTop: "10px", marginLeft: 0, justifyContent: "flex-start" }}>
                          {!profile.is_default ? (
                            <Button
                              onClick={() => void setDefaultProviderProfile(profile.id).then(() => refresh()).catch(() => setFeedback("默认 Provider 未能更新。"))}
                              size="compact"
                              variant="ghost"
                            >
                              设为默认
                            </Button>
                          ) : null}
                          {authModes.includes("oauth") ? (
                            <Button
                              onClick={() => ask("开始 Provider 授权", "将打开 Provider 支持的 OAuth 或设备码授权流程。", () => startAuthorization(profile.id))}
                              size="compact"
                              variant="secondary"
                            >
                              重新认证
                            </Button>
                          ) : null}
                          {authModes.includes("api_key") ? (
                            <>
                              <Field className="task6-provider-credential-field" label="更换 API key" hint="仅本次提交保存在内存，提交后立即清空。">
                                <FieldControl
                                  autoComplete="off"
                                  onChange={(event) => setCredentialDrafts((current) => ({ ...current, [profile.id]: event.target.value }))}
                                  type="password"
                                  value={credentialDrafts[profile.id] ?? ""}
                                />
                              </Field>
                              <Button
                                disabled={!credentialDrafts[profile.id]}
                                onClick={() => ask("更换 Provider credential", "现有 credential 将被替换，Coach 连接可能需要重新测试。", async () => {
                                  await setProviderApiKey(profile.id, credentialDrafts[profile.id] ?? "");
                                  setCredentialDrafts((current) => ({ ...current, [profile.id]: "" }));
                                })}
                                size="compact"
                                variant="secondary"
                              >
                                更换
                              </Button>
                            </>
                          ) : null}
                          {profile.credential_configured ? (
                            <Button
                              onClick={() => ask("移除 Provider credential", "移除或撤销认证后 Coach 将不可用，本地分析不受影响。", async () => { await deleteProviderCredential(profile.id); })}
                              size="compact"
                              variant="ghost"
                            >
                              移除认证
                            </Button>
                          ) : null}
                          <Button
                            onClick={() => ask("删除 Provider", "删除此本地 Provider 配置与 credential，不会删除 Analysis。", async () => { await deleteProviderProfile(profile.id); })}
                            size="compact"
                            variant="danger"
                          >
                            删除
                          </Button>
                        </div>
                      ) : null}
                    </div>
                  </article>
                );
              })}
              {authOperation ? (
                <section aria-live="polite" className="task6-auth-operation">
                  <div className="task6-auth-operation-head">
                    <span className="task6-auth-operation-title">Provider 授权</span>
                    <Status tone={authOperation.status === "succeeded" ? "success" : authOperation.status === "failed" || authOperation.status === "timed_out" || authOperation.status === "interrupted" ? "error" : "info"}>
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
              <div className="task6-provider-form">
                <h3 className="task6-provider-form-title">添加 Provider</h3>
                <Field label="类型">
                  <select className="ac-field__control" onChange={(event) => {
                    const nextProviderId = event.target.value;
                    setProviderId(nextProviderId);
                    if (nextProviderId === "custom") resetCustomModels();
                  }} value={providerId || selectedCatalogProvider?.provider_id || ""}>
                    {catalog?.providers.map((provider) => <option key={provider.provider_id} value={provider.provider_id}>{provider.provider_name}</option>)}
                    <option value="custom">自定义 Provider</option>
                  </select>
                </Field>
                <Field label="显示名称"><FieldControl onChange={(event) => setProfileName(event.target.value)} value={profileName} /></Field>
                {customProvider ? <Field label="Base URL"><FieldControl onChange={(event) => { setBaseUrl(event.target.value); resetCustomModels(); }} placeholder={customKind === "custom_anthropic_compatible" ? "https://provider.example" : "https://provider.example/v1"} value={baseUrl} /></Field> : null}
                {customProvider ? (
                  <>
                    <Field label="API key"><FieldControl autoComplete="off" onChange={(event) => { setNewApiKey(event.target.value); resetCustomModels(); }} type="password" value={newApiKey} /></Field>
                    {customProtocolNeedsChoice ? <Field label="接口协议">
                      <select onChange={(event) => {
                        setCustomKind(event.target.value as CustomProviderKind);
                        setCustomProtocolNeedsChoice(true);
                        setCustomProtocolConfirmed(true);
                      }} value={customKind}>
                        <option value="custom_openai_compatible">OpenAI-compatible</option>
                        <option value="custom_anthropic_compatible">Anthropic-compatible</option>
                      </select>
                    </Field> : null}
                    {customModelState === "loading" ? <p className="task6-muted" aria-live="polite">正在读取可用模型…</p> : null}
                    {customModelMessage ? <p className="task6-muted" aria-live="polite">{customModelMessage}</p> : null}
                    {customModelState === "loaded" ? (
                      <Field label="Model">
                        <select onChange={(event) => setModelId(event.target.value)} value={modelId}>
                          <option value="">选择 Model</option>
                          {customModels.map((model) => <option key={model} value={model}>{model}</option>)}
                        </select>
                        <Button onClick={() => { setModelId(""); setCustomModelState("manual"); setCustomModelMessage(""); }} size="compact" variant="ghost">列表中没有需要的 Model ID</Button>
                      </Field>
                    ) : null}
                    {customModelState === "manual" ? <Field label="Model ID"><FieldControl autoComplete="off" onChange={(event) => setModelId(event.target.value)} value={modelId} /></Field> : null}
                  </>
                ) : null}
                {!customProvider && selectedCatalogProvider ? (
                  <Field label="认证方式">
                    <select className="ac-field__control" onChange={(event) => setNewAuthMode(event.target.value as ProviderAuthMode)} value={newAuthMode}>
                      {selectedCatalogProvider.auth_modes.map((mode) => <option key={mode} value={mode}>{mode === "api_key" ? "API Key" : mode === "oauth" ? "OAuth / 设备码" : "环境凭据"}</option>)}
                    </select>
                  </Field>
                ) : null}
                {!customProvider && newAuthMode === "api_key" ? <Field label="API key"><FieldControl autoComplete="off" onChange={(event) => setNewApiKey(event.target.value)} type="password" value={newApiKey} /></Field> : null}
                {!customProvider && selectedCatalogProvider && (newAuthMode !== "api_key" || newApiKey.trim()) ? (
                  <Field label="Model">
                    <select onChange={(event) => setModelId(event.target.value)} value={modelId}>
                      <option value="">选择 Model</option>
                      {selectedCatalogProvider.models.map((model) => <option key={model.model_id} value={model.model_id}>{model.model_name ?? model.model_id}</option>)}
                    </select>
                  </Field>
                ) : null}
                <div className="task6-inline-actions">
                  <Button disabled={!canAddProvider} onClick={() => void addProfile().catch(() => setFeedback("Provider 未能添加，请检查输入后重试。"))}>添加 Provider</Button>
                </div>
              </div>
            </Panel>
          </section>

          <div className="task6-settings-grid-2">
            <section className="task6-settings-section" id="profile">
              <div className="task6-settings-section-header">
                <span className="task6-settings-section-title">Profile</span>
                <span className="task6-settings-section-hint">配置档默认值</span>
              </div>
              <Panel>
                <div className="task6-field-grid">
                  <Field label="cm/360" hint="仅作为 profile_default"><FieldControl inputMode="decimal" min="0.01" onChange={(event) => setCmPer360(event.target.value)} step="any" type="number" value={cmPer360} /></Field>
                  <Field label="FOV" hint="仅作为 profile_default"><FieldControl inputMode="decimal" max="180" min="0.01" onChange={(event) => setFov(event.target.value)} step="any" type="number" value={fov} /></Field>
                  <Button disabled={!cmPer360 && !fov} onClick={() => void saveProfileCalibration().catch(() => setFeedback("配置档未能保存，请检查数值。"))} size="compact" variant="secondary">保存</Button>
                </div>
                <p className="task6-muted">Stats 自动读取优先，此处仅在读取失败时使用。已完成分析冻结当时数值，改这里不影响历史；无法推导时显示「无法确定」，不猜值。</p>
                <p className="task6-muted">DPI：{calibration?.dpi ?? "待读取"} · Sensitivity：{calibration?.sensitivity ?? "待读取"}</p>
                <div className="task6-inline-actions">
                  <Button onClick={() => ask("删除配置档默认值", "之后仍会优先使用 Stats 或本局手动覆盖。", async () => { await deleteCalibrationProfile(); })} variant="ghost">删除</Button>
                </div>
              </Panel>
            </section>

            <section className="task6-settings-section" id="theme">
              <div className="task6-settings-section-header">
                <span className="task6-settings-section-title">主题</span>
                <span className="task6-settings-section-hint">偏好只保存在本机</span>
              </div>
              <div className="task6-theme-options">
                {themeOptions.map((mode) => (
                  <label className="task6-mode-card" data-selected={preference === mode.value} key={mode.value} onClick={() => setPreference(mode.value)}>
                    <input checked={preference === mode.value} name="theme" onChange={() => setPreference(mode.value)} type="radio" value={mode.value} />
                    <span className="task6-mode-card-name">{mode.label}</span>
                  </label>
                ))}
              </div>
            </section>
          </div>

          <section className="task6-settings-section" id="capture">
            <div className="task6-settings-section-header">
              <span className="task6-settings-section-title">自动采集与 Raw Input</span>
              <span className="task6-settings-section-hint">各项状态分开表达，不是一个总开关</span>
            </div>
            <Panel>
              {!desktop ? <Notice className="task6-settings-notice" tone="warning" title="浏览器模式">自动采集、Raw Input、硬件回放缓冲和权限管理仅在 Desktop 可用。</Notice> : null}
              {capture?.availability === "unavailable" ? <Notice className="task6-settings-notice" tone="error" title="采集状态不可用">{capture.error?.message ?? "本地采集服务暂时不可用。"}</Notice> : null}
              {capture ? (
                <dl className="task6-st-rows">
                  <div className="task6-st-row">
                    <dt>平台支持</dt>
                    <dd><span className="task6-ok">{captureLabel(capture.platform_supported, "✓ Windows", "✗ 不支持")}</span>（非 Windows 提供视频兼容路径）</dd>
                  </div>
                  <div className="task6-st-row">
                    <dt>Raw Input 授权</dt>
                    <dd>{rawPermissionLabel(capture.raw_input_permission)} — 只采集 KovaaK 进程内的相对鼠标输入；不采集键盘与桌面坐标；只保存在本机。<a href="#">查看范围说明</a> · <a href="#">关闭授权</a></dd>
                  </div>
                  <div className="task6-st-row">
                    <dt>自动采集</dt>
                    <dd><span className="task6-ok">{captureLabel(capture.capture_enabled, "待命", "已关闭")}</span> — 检测到 KovaaK 进程后开始采集</dd>
                  </div>
                  <div className="task6-st-row">
                    <dt>KovaaK 进程</dt>
                    <dd>{captureLabel(capture.kovaak_process_present, "已检测到", "未运行")}</dd>
                  </div>
                  <div className="task6-st-row">
                    <dt>回放缓冲</dt>
                    <dd>{captureLabel(capture.replay_buffer_active, "维护中", "未活动")} — 仅保留最近 300 秒、仅 KovaaK 窗口画面，不录桌面与其它窗口</dd>
                  </div>
                  <div className="task6-st-row">
                    <dt>暂停局处理</dt>
                    <dd>Stats 显示暂停的对局不生成永久录像，证据保留为部分/不可用（fail-closed）</dd>
                  </div>
                </dl>
              ) : null}
              <p className="task6-muted">如需关闭 Raw Input 授权，请在 Windows 设置的隐私与应用权限中关闭本应用权限；关闭自动采集不会删除历史 trace。</p>
              {desktop && capture?.capture_enabled != null ? (
                <div className="task6-inline-actions" style={{ marginTop: "12px" }}>
                  <Button onClick={() => void setDesktopCaptureEnabled(!capture.capture_enabled).then(() => refresh())} variant="secondary">
                    {capture.capture_enabled ? "关闭未来采集" : "启用自动采集"}
                  </Button>
                </div>
              ) : null}
            </Panel>
          </section>

          <section className="task6-settings-section" id="kovaak">
            <div className="task6-settings-section-header">
              <span className="task6-settings-section-title">KovaaK 成绩</span>
              <span className="task6-settings-section-hint">读取一组已审核训练项目的最近成绩</span>
            </div>
            <Panel>
              <KovaaKConnectionPanel context="settings" />
            </Panel>
          </section>

          <section className="task6-settings-section" id="storage">
            <div className="task6-settings-section-header">
              <span className="task6-settings-section-title">存储</span>
              <span className="task6-settings-section-hint">总占用 {formatBytes(totalBytes)} · 手动分类管理，不提供自动清理</span>
            </div>
            <Panel>
              {!desktop ? <Notice className="task6-settings-notice" tone="warning" title="Desktop 能力不可用">浏览器不会伪造本地占用或删除操作。</Notice> : null}
              {storage ? (
                <>
                  <div className="task6-storage-bar">
                    {storageBar.map((width, index) => (
                      <div key={index} style={{ width: `${width}%`, background: STORAGE_COLORS[index % STORAGE_COLORS.length] }} />
                    ))}
                  </div>
                  <dl className="task6-storage-kv">
                    {storageCategories.map(([label, bytes], index) => (
                      <div key={label}>
                        <dt><span className="task6-storage-swatch" style={{ background: STORAGE_COLORS[index % STORAGE_COLORS.length] }} />{label}</dt>
                        <dd><span className="task6-mono">{formatBytes(bytes)} · {totalBytes > 0 ? `${Math.round(bytes / totalBytes * 100)}%` : "0%"}</span> · {index === 0 || index === 2 ? <a href="#">管理…</a> : null}</dd>
                      </div>
                    ))}
                  </dl>
                </>
              ) : null}
              {runs.filter((run) => run.video_artifact_ref || run.trace_quality.state === "attached").map((run) => (
                <article className="task6-storage-row" key={run.run_ref}>
                  <div>
                    <strong>{run.scenario ?? "未知场景"}</strong>
                    <p>{run.video_artifact_ref && run.trace_quality.state === "attached" ? "录像与 Raw trace" : run.video_artifact_ref ? "Run 录像" : "Raw trace"}</p>
                  </div>
                  <div className="task6-inline-actions">
                    {run.video_artifact_ref ? <Button onClick={() => ask("移除 Run 录像", "录像引用将变为 unavailable；Run metadata、Analysis 与用户源文件保留。", async () => { await removeRunEvidence(run.id, "video"); })} size="compact" variant="danger">移除录像</Button> : null}
                    {run.trace_quality.state === "attached" ? <Button onClick={() => ask("移除 Raw trace", "依赖 Raw 的证据引用将变为 unavailable；Run metadata 与用户源文件保留。", async () => { await removeRunEvidence(run.id, "raw"); })} size="compact" variant="danger">移除 Raw</Button> : null}
                  </div>
                </article>
              ))}
              {incomplete.map((item) => (
                <article className="task6-storage-row" key={item.item_ref}>
                  <div>
                    <strong>未完成采集</strong>
                    <p>{formatBytes(item.size_bytes)} · {incompleteReasonLabel(item.reason)}</p>
                  </div>
                  <Button disabled={!item.removable} onClick={() => ask("移除未完成采集", item.impact.message, async () => { await removeIncompleteCapture(item.item_ref); })} size="compact" variant="danger">移除</Button>
                </article>
              ))}
            </Panel>
          </section>
        </div>
      </div>

      <Dialog
        footer={<><Button onClick={() => setConfirmAction(null)} variant="secondary">取消</Button><Button onClick={() => void confirm()} variant="danger">确认</Button></>}
        onClose={() => setConfirmAction(null)}
        open={Boolean(confirmAction)}
        title={confirmAction?.title ?? "确认操作"}
      >
        <p>{confirmAction?.impact}</p>
      </Dialog>
      {feedback ? <Toast onClose={() => setFeedback(null)}>{feedback}</Toast> : null}
    </div>
  );
}
