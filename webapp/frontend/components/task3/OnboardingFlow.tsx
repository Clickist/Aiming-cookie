"use client";

import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import {
  authorizeProviderProfile,
  completeOnboarding,
  createProviderProfile,
  getCaptureStatus,
  getDefaultProviderStatus,
  getProviderAuthCapabilities,
  getProviderAuthOperation,
  getProviderCatalog,
  submitProviderAuthInput,
  testProviderProfile,
} from "@/lib/api";
import { isDesktopRuntime, setDesktopCaptureEnabled } from "@/lib/desktop";
import type {
  CaptureStatusV1,
  ProviderAuthMode,
  ProviderAuthOperation,
  ProviderCatalogEntry,
} from "@/lib/types";
import { Button, Field, FieldControl, Notice, Status } from "@/ui/primitives";

type ConnectionState = "idle" | "loading" | "authorizing" | "testing" | "ready" | "failed";

function firstAuthMode(provider: ProviderCatalogEntry | undefined): ProviderAuthMode {
  if (provider?.auth_modes.includes("api_key")) return "api_key";
  if (provider?.auth_modes.includes("oauth")) return "oauth";
  return "ambient";
}

function isAuthTerminal(operation: ProviderAuthOperation): boolean {
  return ["succeeded", "failed", "cancelled", "timed_out", "interrupted"].includes(operation.status);
}

export function OnboardingFlow() {
  const router = useRouter();
  const [step, setStep] = useState<1 | 2>(1);
  const [providers, setProviders] = useState<ProviderCatalogEntry[]>([]);
  const [providerId, setProviderId] = useState("");
  const [modelId, setModelId] = useState("");
  const [authMode, setAuthMode] = useState<ProviderAuthMode>("api_key");
  const [apiKey, setApiKey] = useState("");
  const [custom, setCustom] = useState(false);
  const [customName, setCustomName] = useState("本地 OpenAI-compatible");
  const [customBaseUrl, setCustomBaseUrl] = useState("http://127.0.0.1:11434/v1");
  const [customModel, setCustomModel] = useState("");
  const [connectionState, setConnectionState] = useState<ConnectionState>("loading");
  const [profileId, setProfileId] = useState<number | null>(null);
  const [operation, setOperation] = useState<ProviderAuthOperation | null>(null);
  const [promptValue, setPromptValue] = useState("");
  const [message, setMessage] = useState("");
  const [captureOptIn, setCaptureOptIn] = useState(false);
  const [captureStatus, setCaptureStatus] = useState<CaptureStatusV1 | null>(null);
  const [finishing, setFinishing] = useState(false);
  const [desktop, setDesktop] = useState(false);

  const selectedProvider = useMemo(
    () => providers.find((provider) => provider.provider_id === providerId),
    [providerId, providers],
  );

  useEffect(() => {
    setDesktop(isDesktopRuntime());
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void Promise.allSettled([
      getProviderCatalog({ signal: controller.signal }),
      getProviderAuthCapabilities({ signal: controller.signal }),
      getDefaultProviderStatus({ signal: controller.signal }),
    ]).then(([catalogResult, capabilityResult, statusResult]) => {
      if (controller.signal.aborted) return;
      if (catalogResult.status === "fulfilled") {
        const catalogProviders = catalogResult.value.providers;
        if (capabilityResult.status === "fulfilled") {
          const modes = new Map(capabilityResult.value.providers.map((item) => [item.provider_id, item.auth_modes]));
          setProviders(catalogProviders.map((provider) => ({
            ...provider,
            auth_modes: modes.get(provider.provider_id) ?? provider.auth_modes,
          })));
        } else {
          setProviders(catalogProviders);
        }
        const first = catalogProviders[0];
        if (first) {
          setProviderId(first.provider_id);
          setModelId(first.models[0]?.model_id ?? "");
          setAuthMode(firstAuthMode(first));
        }
      } else {
        setMessage("Provider 目录暂时不可用，请稍后重试。");
      }
      if (statusResult.status === "fulfilled" && statusResult.value.status === "ready") {
        setProfileId(statusResult.value.profile_id);
        setConnectionState("ready");
        setMessage("现有 Provider 已连接，可以继续。");
      } else {
        setConnectionState("idle");
      }
    });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (!selectedProvider) return;
    setModelId(selectedProvider.models[0]?.model_id ?? "");
    setAuthMode(firstAuthMode(selectedProvider));
  }, [selectedProvider]);

  useEffect(() => {
    if (!operation || isAuthTerminal(operation)) return;
    const timer = window.setTimeout(() => {
      void getProviderAuthOperation(operation.operation_id)
        .then(async (next) => {
          setOperation(next);
          if (next.status === "succeeded" && profileId !== null) {
            setConnectionState("testing");
            const status = await testProviderProfile(profileId);
            setConnectionState(status.status === "ready" ? "ready" : "failed");
            setMessage(status.status === "ready" ? "Provider 已连接并通过测试。" : status.message);
          } else if (["failed", "cancelled", "timed_out", "interrupted"].includes(next.status)) {
            setConnectionState("failed");
            setMessage("Provider 认证未完成，可重新尝试。");
          }
        })
        .catch(() => {
          setConnectionState("failed");
          setMessage("认证状态暂时无法读取，可重新尝试。");
        });
    }, 900);
    return () => window.clearTimeout(timer);
  }, [operation, profileId]);

  useEffect(() => {
    if (step !== 2 || !desktop) return;
    void getCaptureStatus()
      .then(setCaptureStatus)
      .catch(() => setCaptureStatus(null));
  }, [desktop, step]);

  const connect = async () => {
    setConnectionState("testing");
    setMessage("");
    setOperation(null);
    try {
      const profile = custom
        ? await createProviderProfile({
            name: customName,
            kind: "custom_openai_compatible",
            base_url: customBaseUrl,
            model_id: customModel,
            api_key: apiKey,
            is_default: true,
          })
        : await createProviderProfile({
            name: selectedProvider?.provider_name ?? providerId,
            kind: "builtin",
            provider_id: providerId,
            model_id: modelId,
            api_key: authMode === "api_key" ? apiKey : undefined,
            is_default: true,
          });
      setProfileId(profile.id);
      if (!custom && authMode === "oauth") {
        setConnectionState("authorizing");
        const next = await authorizeProviderProfile(profile.id, "oauth");
        setOperation(next);
        setMessage("请按 Provider 指引完成授权。");
        return;
      }
      const status = await testProviderProfile(profile.id);
      setConnectionState(status.status === "ready" ? "ready" : "failed");
      setMessage(status.status === "ready" ? "Provider 已连接并通过测试。" : status.message);
      setApiKey("");
    } catch {
      setConnectionState("failed");
      setMessage("连接失败。请检查 Provider、模型和认证信息后重试。");
    }
  };

  const submitPrompt = async () => {
    const prompt = operation?.prompts[0];
    if (!operation || !prompt || !promptValue) return;
    try {
      const next = await submitProviderAuthInput(operation.operation_id, prompt.prompt_id, promptValue);
      setPromptValue("");
      setOperation(next);
    } catch {
      setMessage("认证输入未被接受，请重试。");
    }
  };

  const skip = async () => {
    setFinishing(true);
    try {
      const state = await completeOnboarding("skipped");
      if (state.availability !== "available") throw new Error("unavailable");
      router.push("/analyze");
    } catch {
      setMessage("暂时无法保存跳过决定，请重试。");
      setFinishing(false);
    }
  };

  const finish = async () => {
    setFinishing(true);
    setMessage("");
    try {
      if (captureOptIn) {
        await setDesktopCaptureEnabled(true);
      }
      const state = await completeOnboarding("connected");
      if (state.availability !== "available") throw new Error("unavailable");
      router.push("/analyze");
    } catch {
      setMessage("设置未能完整保存。没有假装自动采集已启用，请重试。");
      setFinishing(false);
    }
  };

  return (
    <main className="task3-onboarding" id="main-content">
      <div className="task3-onboarding-brand">Aiming Cookie</div>
      <div className="task3-onboarding-progress" aria-label={`第 ${step} 步，共 2 步`}>
        <span data-active={step === 1 || undefined}>1</span>
        <i />
        <span data-active={step === 2 || undefined}>2</span>
      </div>

      {step === 1 ? (
        <section className="task3-onboarding-sheet" aria-labelledby="provider-title">
          <div className="task3-eyebrow">第一步 · Coach Provider</div>
          <h1 id="provider-title">连接你自己的 AI Provider</h1>
          <p className="task3-lead">Aiming Cookie 本身开源免费。第三方 Provider 可能按其规则收费；未连接时，本地指标、确定性诊断和历史记录仍可正常使用。</p>
          <Notice title="默认数据边界">
            Provider 只接收产品合同允许的分析摘要与证据引用；本地视频、Raw Input、文件路径和密钥不会进入 Coach 对话。
          </Notice>

          {connectionState === "ready" ? (
            <div className="task3-connected-row">
              <Status tone="success">已连接</Status>
              <span>{message}</span>
              <Button onClick={() => setStep(2)}>继续</Button>
            </div>
          ) : (
            <>
              <div className="task3-provider-tabs" role="tablist" aria-label="Provider 类型">
                <button aria-selected={!custom} onClick={() => setCustom(false)} role="tab" type="button">Provider 目录</button>
                <button aria-selected={custom} onClick={() => setCustom(true)} role="tab" type="button">自定义 / 本地</button>
              </div>

              <div className="task3-form-grid">
                {custom ? (
                  <>
                    <Field label="名称"><FieldControl onChange={(event) => setCustomName(event.target.value)} value={customName} /></Field>
                    <Field label="Base URL"><FieldControl onChange={(event) => setCustomBaseUrl(event.target.value)} value={customBaseUrl} /></Field>
                    <Field label="模型 ID"><FieldControl onChange={(event) => setCustomModel(event.target.value)} value={customModel} /></Field>
                  </>
                ) : (
                  <>
                    <Field label="Provider">
                      <select className="ac-field__control" onChange={(event) => setProviderId(event.target.value)} value={providerId}>
                        {providers.map((provider) => <option key={provider.provider_id} value={provider.provider_id}>{provider.provider_name}</option>)}
                      </select>
                    </Field>
                    <Field label="模型">
                      <select className="ac-field__control" onChange={(event) => setModelId(event.target.value)} value={modelId}>
                        {selectedProvider?.models.map((model) => <option key={model.model_id} value={model.model_id}>{model.model_name ?? model.model_id}</option>)}
                      </select>
                    </Field>
                    <div className="task3-auth-modes" role="radiogroup" aria-label="认证方式">
                      {selectedProvider?.auth_modes.map((mode) => (
                        <label key={mode}>
                          <input checked={authMode === mode} name="auth-mode" onChange={() => setAuthMode(mode)} type="radio" />
                          <span>{mode === "api_key" ? "API Key" : mode === "oauth" ? "OAuth / 设备码" : "环境凭据"}</span>
                        </label>
                      ))}
                    </div>
                  </>
                )}
                {(custom || authMode === "api_key") ? (
                  <Field hint="只随本次 HTTPS/本地回环请求提交；保存后不会回显。" label="API Key">
                    <FieldControl autoComplete="off" onChange={(event) => setApiKey(event.target.value)} type="password" value={apiKey} />
                  </Field>
                ) : null}
              </div>

              {operation ? (
                <div className="task3-auth-operation" aria-live="polite">
                  {operation.events.map((event, index) => (
                    <div key={`${event.type}-${index}`}>
                      {event.type === "auth_url" ? <a href={event.url} rel="noreferrer" target="_blank">打开 Provider 授权页</a> : null}
                      {event.type === "device_code" ? <p>设备码：<strong>{event.user_code}</strong> · <a href={event.verification_uri} rel="noreferrer" target="_blank">前往验证</a></p> : null}
                      {event.type === "progress" ? <p>{event.message}</p> : null}
                    </div>
                  ))}
                  {operation.prompts[0] ? (
                    <Field label={operation.prompts[0].message}>
                      <div className="task3-inline-field">
                        <FieldControl autoComplete="off" onChange={(event) => setPromptValue(event.target.value)} type={operation.prompts[0].type === "secret" ? "password" : "text"} value={promptValue} />
                        <Button onClick={() => void submitPrompt()} variant="secondary">提交</Button>
                      </div>
                    </Field>
                  ) : null}
                </div>
              ) : null}

              {message ? <Notice tone={connectionState === "failed" ? "error" : "info"}>{message}</Notice> : null}
              <div className="task3-onboarding-actions">
                <Button disabled={connectionState === "testing" || connectionState === "authorizing" || (custom ? !customModel || !customBaseUrl || !apiKey : !providerId || !modelId || (authMode === "api_key" && !apiKey))} onClick={() => void connect()}>
                  {connectionState === "testing" ? "正在测试连接" : connectionState === "authorizing" ? "等待授权" : "连接并测试"}
                </Button>
              </div>
            </>
          )}

          <button className="task3-skip-action" disabled={finishing} onClick={() => void skip()} title="跳过后没有任何 Coach 功能；本地分析、确定性诊断和 History 仍可使用。" type="button">
            暂不连接，使用本地模式
          </button>
        </section>
      ) : (
        <section className="task3-onboarding-sheet" aria-labelledby="capture-title">
          <div className="task3-eyebrow">第二步 · 自动采集</div>
          <h1 id="capture-title">训练后自动整理证据</h1>
          <p className="task3-lead">桌面版可在 KovaaK 运行时准备 300 秒硬件编码回放缓冲，并优先保留 Raw Input。每一局完成后仍由你确认要分析哪一条 Run。</p>
          {desktop ? (
            <label className="task3-opt-in-row">
              <input checked={captureOptIn} onChange={(event) => setCaptureOptIn(event.target.checked)} type="checkbox" />
              <span><strong>启用自动采集</strong><small>仅采集 KovaaK 窗口；暂停局按 fail-closed 处理，不生成误导性证据。</small></span>
            </label>
          ) : (
            <Notice title="当前是浏览器预览">自动采集、Raw Input 和桌面文件选择只在 Windows 桌面版可用；页面结构保持一致。</Notice>
          )}
          {captureStatus?.availability === "available" ? (
            <div className="task3-capture-facts">
              <span>平台支持 <strong>{captureStatus.platform_supported ? "是" : "否"}</strong></span>
              <span>Raw Input 授权 <strong>{captureStatus.raw_input_permission === "granted" ? "已授权" : captureStatus.raw_input_permission === "denied" ? "已拒绝" : "待确认"}</strong></span>
              <span>当前采集 <strong>{captureStatus.capture_enabled ? "已启用" : "未启用"}</strong></span>
            </div>
          ) : null}
          {message ? <Notice tone="error">{message}</Notice> : null}
          <div className="task3-onboarding-actions">
            <Button onClick={() => setStep(1)} variant="secondary">返回</Button>
            <Button disabled={finishing || (captureOptIn && !desktop)} onClick={() => void finish()}>{finishing ? "正在保存" : "进入工作台"}</Button>
          </div>
        </section>
      )}
    </main>
  );
}
