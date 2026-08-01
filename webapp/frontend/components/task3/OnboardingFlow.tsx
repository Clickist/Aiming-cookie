"use client";

import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";

import {
  authorizeProviderProfile,
  completeOnboarding,
  createProviderProfile,
  discoverCustomProviderModels,
  getCaptureStatus,
  getDefaultProviderStatus,
  getProviderAuthCapabilities,
  getProviderAuthOperation,
  getProviderCatalog,
  listProviderProfiles,
  submitProviderAuthInput,
  testProviderProfile,
} from "@/lib/api";
import { isDesktopRuntime, setDesktopCaptureEnabled } from "@/lib/desktop";
import type {
  CaptureStatusV1,
  CustomProviderKind,
  CustomProviderProtocol,
  ProviderAuthMode,
  ProviderAuthOperation,
  ProviderCatalogEntry,
  ProviderProfile,
} from "@/lib/types";
import { Button, Field, FieldControl, Notice } from "@/ui/primitives";
import { KovaaKConnectionPanel } from "@/components/kovaak/KovaaKConnectionPanel";

type ConnectionState = "idle" | "loading" | "authorizing" | "testing" | "ready" | "failed";
type OpenMenu = "provider" | "protocol" | "model" | null;
type CustomModelState = "idle" | "loading" | "loaded" | "manual";

const CUSTOM_PROVIDER_ID = "custom";
const CUSTOM_PROTOCOLS: Record<CustomProviderKind, { label: string; discovery: CustomProviderProtocol }> = {
  custom_openai_compatible: {
    label: "OpenAI-compatible",
    discovery: "openai-completions",
  },
  custom_anthropic_compatible: {
    label: "Anthropic-compatible",
    discovery: "anthropic-messages",
  },
};

function customKindForProtocol(protocol: CustomProviderProtocol): CustomProviderKind {
  return protocol === "anthropic-messages" ? "custom_anthropic_compatible" : "custom_openai_compatible";
}

function isCustomProviderKind(kind: string): kind is CustomProviderKind {
  return kind === "custom_openai_compatible" || kind === "custom_anthropic_compatible";
}

function firstAuthMode(provider: ProviderCatalogEntry | undefined): ProviderAuthMode {
  if (provider?.auth_modes.includes("api_key")) return "api_key";
  if (provider?.auth_modes.includes("oauth")) return "oauth";
  return "ambient";
}

function isAuthTerminal(operation: ProviderAuthOperation): boolean {
  return ["succeeded", "failed", "cancelled", "timed_out", "interrupted"].includes(operation.status);
}

function authModeLabel(mode: ProviderAuthMode): string {
  if (mode === "api_key") return "API Key";
  if (mode === "oauth") return "OAuth / 设备码";
  return "环境凭据";
}

export function OnboardingFlow() {
  const router = useRouter();
  const [step, setStep] = useState<1 | 2 | 3>(1);
  const [providers, setProviders] = useState<ProviderCatalogEntry[]>([]);
  const [providerId, setProviderId] = useState("");
  const [modelId, setModelId] = useState("");
  const [authMode, setAuthMode] = useState<ProviderAuthMode>("api_key");
  const [apiKey, setApiKey] = useState("");
  const [custom, setCustom] = useState(false);
  const [customKind, setCustomKind] = useState<CustomProviderKind>("custom_openai_compatible");
  const [customBaseUrl, setCustomBaseUrl] = useState("");
  const [customModel, setCustomModel] = useState("");
  const [customModels, setCustomModels] = useState<string[]>([]);
  const [customModelState, setCustomModelState] = useState<CustomModelState>("idle");
  const [customModelMessage, setCustomModelMessage] = useState("");
  const [customModelError, setCustomModelError] = useState(false);
  const [customProtocolNeedsChoice, setCustomProtocolNeedsChoice] = useState(false);
  const [customProtocolConfirmed, setCustomProtocolConfirmed] = useState(false);
  const [openMenu, setOpenMenu] = useState<OpenMenu>(null);
  const [connectionState, setConnectionState] = useState<ConnectionState>("loading");
  const [profileId, setProfileId] = useState<number | null>(null);
  const [savedProfile, setSavedProfile] = useState<ProviderProfile | null>(null);
  const [operation, setOperation] = useState<ProviderAuthOperation | null>(null);
  const [promptValue, setPromptValue] = useState("");
  const [message, setMessage] = useState("");
  const [catalogUnavailable, setCatalogUnavailable] = useState(false);
  const [captureOptIn, setCaptureOptIn] = useState(false);
  const [captureStatus, setCaptureStatus] = useState<CaptureStatusV1 | null>(null);
  const [finishing, setFinishing] = useState(false);
  const [desktop, setDesktop] = useState(false);
  const providerMenuRef = useRef<HTMLDivElement>(null);
  const protocolMenuRef = useRef<HTMLDivElement>(null);
  const modelMenuRef = useRef<HTMLDivElement>(null);

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
      listProviderProfiles({ signal: controller.signal }),
    ]).then(([catalogResult, capabilityResult, statusResult, profilesResult]) => {
      if (controller.signal.aborted) return;
      if (catalogResult.status === "fulfilled") {
        setCatalogUnavailable(false);
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
      } else {
        setCatalogUnavailable(true);
        setMessage("Provider 目录暂时不可用，请稍后重试。");
      }
      if (statusResult.status === "fulfilled" && statusResult.value.status === "ready") {
        setProfileId(statusResult.value.profile_id);
        setConnectionState("ready");
        setCatalogUnavailable(false);
        setMessage("连接成功 · 已保存的 Provider");
        if (profilesResult.status === "fulfilled") {
          const profile = profilesResult.value.profiles.find((item) => item.id === statusResult.value.profile_id) ?? null;
          setSavedProfile(profile);
          if (profile && isCustomProviderKind(profile.kind)) {
            setCustom(true);
            setCustomKind(profile.kind);
            setCustomProtocolConfirmed(true);
            setCustomBaseUrl(profile.base_url ?? "");
            setCustomModel(profile.model_id);
          } else if (profile) {
            setProviderId(profile.provider_id);
            setModelId(profile.model_id);
          }
        }
      } else {
        setConnectionState("idle");
      }
    });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (!selectedProvider) return;
    setAuthMode(firstAuthMode(selectedProvider));
  }, [selectedProvider]);

  useEffect(() => {
    if (!custom || customProtocolNeedsChoice || !customBaseUrl.trim() || !apiKey.trim()) return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      setCustomModelState("loading");
      setCustomModel("");
      setCustomModelMessage("");
      setCustomModelError(false);
      void discoverCustomProviderModels({
        base_url: customBaseUrl.trim(),
        api_key: apiKey,
      }, { signal: controller.signal })
        .then((response) => {
          if (controller.signal.aborted) return;
          setCustomKind(customKindForProtocol(response.protocol));
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
          setCustomModelError(true);
        });
    }, 500);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [apiKey, custom, customBaseUrl, customProtocolNeedsChoice]);

  useEffect(() => {
    const closeMenus = (event: MouseEvent) => {
      const target = event.target as Node;
      if (
        providerMenuRef.current?.contains(target)
        || protocolMenuRef.current?.contains(target)
        || modelMenuRef.current?.contains(target)
      ) return;
      setOpenMenu(null);
    };
    document.addEventListener("mousedown", closeMenus);
    return () => document.removeEventListener("mousedown", closeMenus);
  }, []);

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
            setMessage(status.status === "ready" ? `连接成功 · ${custom ? customModel : selectedModelLabel}` : status.message);
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
    if (step !== 3 || !desktop) return;
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
            name: "自定义 Provider",
            kind: customKind,
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
      setMessage(status.status === "ready" ? `连接成功 · ${custom ? customModel : selectedModelLabel}` : status.message);
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

  const selectedModel = selectedProvider?.models.find((model) => model.model_id === modelId);
  const selectedModelLabel = selectedModel?.model_name ?? selectedModel?.model_id ?? modelId;
  const connectionReady = connectionState === "ready";
  const builtinModelSelectable = Boolean(
    selectedProvider
    && (authMode !== "api_key" || apiKey.trim() || (connectionReady && savedProfile?.has_api_key)),
  );
  const menuOpen = openMenu !== null;
  const formComplete = custom
    ? Boolean(customBaseUrl && customModel && apiKey && customProtocolConfirmed)
    : Boolean(providerId && modelId && (authMode !== "api_key" || apiKey));
  const testDisabled = connectionState === "testing"
    || connectionState === "authorizing"
    || (custom && customModelState === "loading")
    || (catalogUnavailable && !custom)
    || menuOpen
    || !formComplete;
  const statusMessage = connectionState === "testing"
    ? "测试中…"
    : connectionState === "authorizing"
      ? "等待授权…"
      : message;
  const statusTone = connectionState === "testing" || connectionState === "authorizing"
    ? "loading"
    : connectionState === "failed" || (catalogUnavailable && !custom)
      ? "error"
      : connectionReady
        ? "success"
        : null;

  const selectProvider = (nextProviderId: string) => {
    setOpenMenu(null);
    setModelId("");
    setApiKey("");
    setMessage("");
    setConnectionState("idle");
    if (nextProviderId === CUSTOM_PROVIDER_ID) {
      setCustom(true);
      setProviderId("");
      setAuthMode("api_key");
      setCustomModel("");
      setCustomModels([]);
      setCustomModelState("idle");
      setCustomModelMessage("");
      setCustomModelError(false);
      setCustomProtocolNeedsChoice(false);
      setCustomProtocolConfirmed(false);
      return;
    }
    const nextProvider = providers.find((provider) => provider.provider_id === nextProviderId);
    setCustom(false);
    setProviderId(nextProviderId);
    setAuthMode(firstAuthMode(nextProvider));
  };

  const selectModel = (nextModelId: string) => {
    setModelId(nextModelId);
    setCustomModel(nextModelId);
    setOpenMenu(null);
    if (!custom) setApiKey("");
    setMessage("");
    setConnectionState("idle");
  };

  const updateCustomConnection = (field: "baseUrl" | "apiKey", value: string) => {
    if (field === "baseUrl") setCustomBaseUrl(value);
    else setApiKey(value);
    setCustomModel("");
    setCustomModels([]);
    setCustomModelState("idle");
    setCustomModelMessage("");
    setCustomModelError(false);
    setCustomProtocolNeedsChoice(false);
    setCustomProtocolConfirmed(false);
  };

  const selectCustomProtocol = (nextKind: CustomProviderKind) => {
    setCustomKind(nextKind);
    setOpenMenu(null);
    setCustomProtocolNeedsChoice(true);
    setCustomProtocolConfirmed(true);
  };

  const useManualCustomModel = () => {
    setOpenMenu(null);
    setCustomModel("");
    setCustomModelState("manual");
    setCustomModelMessage("");
  };

  return (
    <main className="task3-onboarding" id="main-content">
      <div className="task3-onboarding-brand">Aiming Cookie</div>
      <div className="task3-onboarding-progress" aria-label={`第 ${step} 步，共 3 步`}>
        <span data-active={step === 1 || undefined}>1</span>
        <i />
        <span data-active={step === 2 || undefined}>2</span>
        <i />
        <span data-active={step === 3 || undefined}>3</span>
      </div>

      {step === 1 ? (
        <section className="task3-onboarding-sheet task3-onboarding-step" aria-labelledby="provider-title" key="provider">
          <h1 id="provider-title">连接模型服务</h1>

          <div className="task3-onboarding-wizard-fields">
              <Field label="Provider">
                <div className="task3-onboarding-dropdown" ref={providerMenuRef}>
                  <button
                    aria-controls="onboarding-provider-listbox"
                    aria-expanded={openMenu === "provider"}
                    aria-haspopup="listbox"
                    className="task3-onboarding-dropdown-trigger"
                    disabled={connectionReady}
                    onClick={() => setOpenMenu((current) => current === "provider" ? null : "provider")}
                    onKeyDown={(event) => {
                      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
                        event.preventDefault();
                        setOpenMenu("provider");
                      }
                      if (event.key === "Escape") setOpenMenu(null);
                    }}
                    type="button"
                  >
                    <span aria-live="polite">{custom ? "自定义 Provider" : selectedProvider?.provider_name ?? "选择 Provider"}</span>
                  </button>
                  {openMenu === "provider" ? (
                    <div aria-label="Provider 选项" className="task3-onboarding-dropdown-menu" id="onboarding-provider-listbox" role="listbox">
                      {providers.map((provider) => (
                        <button
                          aria-selected={!custom && provider.provider_id === providerId}
                          className="task3-onboarding-dropdown-option"
                          key={provider.provider_id}
                          onClick={() => selectProvider(provider.provider_id)}
                          role="option"
                          type="button"
                        >
                          <span>{provider.provider_name}</span>
                          <small>{provider.auth_modes.map(authModeLabel).join(" / ")}</small>
                        </button>
                      ))}
                      <button
                        aria-selected={custom}
                        className="task3-onboarding-dropdown-option"
                        onClick={() => selectProvider(CUSTOM_PROVIDER_ID)}
                        role="option"
                        type="button"
                      >
                        <span>自定义 Provider</span>
                        <small>填写 URL 和 API key 后自动识别接口</small>
                      </button>
                    </div>
                  ) : null}
                </div>
              </Field>

              {custom ? (
                <div className="task3-onboarding-custom-fields">
                  <Field label="Base URL">
                    <FieldControl autoComplete="url" disabled={connectionReady} onChange={(event) => updateCustomConnection("baseUrl", event.target.value)} placeholder={customKind === "custom_anthropic_compatible" ? "https://provider.example" : "https://provider.example/v1"} value={customBaseUrl} />
                  </Field>
                  <Field label="API key">
                    <FieldControl
                      autoComplete="off"
                      disabled={connectionReady}
                      onChange={(event) => updateCustomConnection("apiKey", event.target.value)}
                      type="password"
                      value={apiKey}
                    />
                  </Field>
                  {customProtocolNeedsChoice ? <Field label="接口协议">
                    <div className="task3-onboarding-dropdown" ref={protocolMenuRef}>
                      <button
                        aria-controls="onboarding-protocol-listbox"
                        aria-expanded={openMenu === "protocol"}
                        aria-haspopup="listbox"
                        className="task3-onboarding-dropdown-trigger"
                        disabled={connectionReady}
                        onClick={() => setOpenMenu((current) => current === "protocol" ? null : "protocol")}
                        onKeyDown={(event) => {
                          if (event.key === "ArrowDown" || event.key === "ArrowUp") {
                            event.preventDefault();
                            setOpenMenu("protocol");
                          }
                          if (event.key === "Escape") setOpenMenu(null);
                        }}
                        type="button"
                      >
                        <span aria-live="polite">{CUSTOM_PROTOCOLS[customKind].label}</span>
                      </button>
                      {openMenu === "protocol" ? (
                        <div aria-label="接口协议选项" className="task3-onboarding-dropdown-menu" id="onboarding-protocol-listbox" role="listbox">
                          {(Object.entries(CUSTOM_PROTOCOLS) as Array<[CustomProviderKind, typeof CUSTOM_PROTOCOLS[CustomProviderKind]]>).map(([kind, protocol]) => (
                            <button
                              aria-selected={kind === customKind}
                              className="task3-onboarding-dropdown-option"
                              key={kind}
                              onClick={() => selectCustomProtocol(kind)}
                              role="option"
                              type="button"
                            >
                              <span>{protocol.label}</span>
                            </button>
                          ))}
                        </div>
                      ) : null}
                    </div>
                  </Field> : null}
                  {customModelState === "loading" || customModelMessage ? (
                    <div className="task3-custom-model-discovery" aria-live="polite">
                      {customModelState === "loading" ? <p data-tone="loading">正在读取可用模型…</p> : null}
                      {customModelMessage ? <p data-tone={customModelError ? "error" : undefined}>{customModelMessage}</p> : null}
                    </div>
                  ) : null}
                  {customModelState === "loaded" ? (
                    <Field label="Model">
                      <div className="task3-onboarding-dropdown" ref={modelMenuRef}>
                        <button
                          aria-controls="onboarding-model-listbox"
                          aria-expanded={openMenu === "model"}
                          aria-haspopup="listbox"
                          className="task3-onboarding-dropdown-trigger"
                          disabled={connectionReady}
                          onClick={() => setOpenMenu((current) => current === "model" ? null : "model")}
                          onKeyDown={(event) => {
                            if (event.key === "ArrowDown" || event.key === "ArrowUp") {
                              event.preventDefault();
                              setOpenMenu("model");
                            }
                            if (event.key === "Escape") setOpenMenu(null);
                          }}
                          type="button"
                        >
                          <span aria-live="polite">{customModel || "选择 Model"}</span>
                        </button>
                        {openMenu === "model" ? (
                          <div aria-label="Model 选项" className="task3-onboarding-dropdown-menu" id="onboarding-model-listbox" role="listbox">
                            <div className="task3-onboarding-dropdown-group" role="group" aria-label="可用 Model">
                              <div className="task3-onboarding-dropdown-label">可用 Model</div>
                              {customModels.map((candidate) => (
                                <button
                                  aria-selected={candidate === customModel}
                                  className="task3-onboarding-dropdown-option"
                                  key={candidate}
                                  onClick={() => selectModel(candidate)}
                                  role="option"
                                  type="button"
                                >
                                  <span>{candidate}</span>
                                </button>
                              ))}
                            </div>
                            <div className="task3-onboarding-dropdown-group">
                              <button className="task3-onboarding-dropdown-option" onClick={useManualCustomModel} type="button">
                                <span>列表中没有需要的 Model ID</span>
                              </button>
                            </div>
                          </div>
                        ) : null}
                      </div>
                    </Field>
                  ) : null}
                  {customModelState === "manual" ? (
                    <Field label="Model ID">
                      <FieldControl autoComplete="off" disabled={connectionReady} onChange={(event) => setCustomModel(event.target.value)} value={customModel} />
                    </Field>
                  ) : null}
                </div>
              ) : null}

              {!custom && selectedProvider && selectedProvider.auth_modes.length > 1 ? (
                <div className="task3-auth-modes" role="radiogroup" aria-label="认证方式">
                  {selectedProvider.auth_modes.map((mode) => (
                    <label key={mode}>
                      <input checked={authMode === mode} disabled={connectionReady} name="auth-mode" onChange={() => setAuthMode(mode)} type="radio" />
                      <span>{authModeLabel(mode)}</span>
                    </label>
                  ))}
                </div>
              ) : null}

              {!custom && authMode === "api_key" ? (
                <Field label="API key">
                  <FieldControl
                    autoComplete="off"
                    disabled={connectionReady}
                    onChange={(event) => setApiKey(event.target.value)}
                    placeholder={connectionReady && savedProfile?.has_api_key ? "已保存的凭据" : undefined}
                    type="password"
                    value={apiKey}
                  />
                </Field>
              ) : null}

              {!custom && builtinModelSelectable ? (
                <Field label="Model">
                  <div className="task3-onboarding-dropdown" ref={modelMenuRef}>
                    <button
                      aria-controls="onboarding-model-listbox"
                      aria-expanded={openMenu === "model"}
                      aria-haspopup="listbox"
                      className="task3-onboarding-dropdown-trigger"
                      disabled={connectionReady}
                      onClick={() => setOpenMenu((current) => current === "model" ? null : "model")}
                      onKeyDown={(event) => {
                        if (event.key === "ArrowDown" || event.key === "ArrowUp") {
                          event.preventDefault();
                          setOpenMenu("model");
                        }
                        if (event.key === "Escape") setOpenMenu(null);
                      }}
                      type="button"
                    >
                      <span aria-live="polite">{selectedModelLabel || "选择 Model"}</span>
                    </button>
                    {openMenu === "model" && selectedProvider ? (
                      <div aria-label="Model 选项" className="task3-onboarding-dropdown-menu" id="onboarding-model-listbox" role="listbox">
                        <div className="task3-onboarding-dropdown-group" role="group" aria-label={selectedProvider.provider_name}>
                          <div className="task3-onboarding-dropdown-label">{selectedProvider.provider_name}</div>
                          {selectedProvider.models.map((model) => (
                            <button
                              aria-selected={model.model_id === modelId}
                              className="task3-onboarding-dropdown-option"
                              key={model.model_id}
                              onClick={() => selectModel(model.model_id)}
                              role="option"
                              type="button"
                            >
                              <span>{model.model_name ?? model.model_id}</span>
                            </button>
                          ))}
                        </div>
                      </div>
                    ) : null}
                  </div>
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

          <div className="task3-onboarding-wizard-actions">
            <div className="task3-onboarding-skip-wrap">
              <button className="task3-skip-action" disabled={finishing} onClick={() => void skip()} type="button">
                暂时不连接
              </button>
              <div className="task3-onboarding-skip-tooltip" role="tooltip">
                跳过后将没有 Coach 对话、AI 解释、长期档案与训练计划；只保留本地指标、确定性诊断、规则化提示和历史。之后可随时在设置中激活。
              </div>
            </div>
            <div aria-atomic="true" aria-live="polite" className="task3-onboarding-status">
              {statusMessage && statusTone ? <span data-tone={statusTone}>{statusMessage}</span> : null}
            </div>
            {connectionReady ? (
              <Button className="task3-onboarding-primary" onClick={() => setStep(2)}>继续</Button>
            ) : (
              <Button className="task3-onboarding-primary" disabled={testDisabled} onClick={() => void connect()}>
                测试连接
              </Button>
            )}
          </div>
        </section>
      ) : step === 2 ? (
        <section className="task3-onboarding-sheet task3-onboarding-step" aria-labelledby="kovaak-title" key="kovaak">
          <div className="task3-eyebrow">第二步 · 可选</div>
          <h1 id="kovaak-title">连接 KovaaK 成绩</h1>
          <p className="task3-lead">可选读取一组训练项目成绩，之后也能在设置中连接、刷新或移除。</p>
          <KovaaKConnectionPanel
            context="onboarding"
            onContinue={() => { setMessage(""); setStep(3); }}
            onSkip={() => { setMessage(""); setStep(3); }}
          />
        </section>
      ) : (
        <section className="task3-onboarding-sheet task3-onboarding-step" aria-labelledby="capture-title" key="capture">
          <div className="task3-eyebrow">第三步 · 自动采集</div>
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
            <Button onClick={() => setStep(2)} variant="secondary">返回</Button>
            <Button disabled={finishing || (captureOptIn && !desktop)} onClick={() => void finish()}>{finishing ? "正在保存" : "进入工作台"}</Button>
          </div>
        </section>
      )}
    </main>
  );
}
