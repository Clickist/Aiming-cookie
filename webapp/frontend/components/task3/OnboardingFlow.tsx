"use client";

/**
 * Onboarding「连接模型服务」（线框 ①/①a/①b，v3.3）。
 *
 * 步骤 1 是 Provider 选择：会员档「Aiming Cookie（推荐）」置顶（①），选中并
 * 「下一步」后整页换成会员登录流（①a 等待 / ①b 三中间态），不走 API key 表单；
 * BYOK（自定义 Provider 与其余目录档）路径与升级前完全一致。
 *
 * 铁律⑤：未连通 Provider 不放行主界面——会员流的「继续」只在连通测试通过后亮。
 * 铁律①：客户端内不出现登录表单、套餐选择与支付界面——一律弹系统浏览器。
 */

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  authorizeProviderProfile,
  completeOnboarding,
  createProviderProfile,
  discoverCustomProviderModels,
  exchangeMemberTicket,
  fetchMemberStatus,
  getCaptureStatus,
  getDefaultProviderStatus,
  getProviderAuthCapabilities,
  getProviderAuthOperation,
  getProviderCatalog,
  listProviderProfiles,
  startMemberLogin,
  submitProviderAuthInput,
  takeProviderAuthResult,
  testProviderProfile,
  updateProviderProfile,
} from "@/lib/api";
import { isDesktopRuntime, openExternalUrl, setDesktopCaptureEnabled } from "@/lib/desktop";
import { MEMBER_COPY, maskEmail } from "@/lib/member";
import { isMemberWizardType, wizardTypeOptions } from "@/lib/provider-wizard";
import { firstAuthMode, isAuthTerminal, isCustomProviderKind, useCustomModelDiscovery } from "@/lib/provider-helpers";
import type {
  CaptureStatusV1,
  CustomProviderKind,
  CustomProviderProtocol,
  MemberMe,
  ProviderAuthMode,
  ProviderAuthOperation,
  ProviderCatalogEntry,
  ProviderProfile,
} from "@/lib/types";
import { Button, Field, FieldControl, Notice } from "@/ui/primitives";
import { startWindowDragging, TauriWindowControls } from "@/components/task3/TauriWindowControls";


/** 授权页等外链：桌面端 opener 唤默认浏览器（WebView2 拦 _blank）。 */
function handleExternalClick(event: { preventDefault(): void }, url: string): void {
  void import("@/lib/desktop").then(({ isDesktopRuntime, openExternalUrl }) => {
    if (isDesktopRuntime()) {
      event.preventDefault();
      void openExternalUrl(url);
    }
  });
}

type ConnectionState = "idle" | "loading" | "authorizing" | "testing" | "ready" | "failed";
type OpenMenu = "provider" | "protocol" | "model" | null;

/** 会员流状态机的四个可见态（①a 等待 / ①b 态1 未订阅 / ①b 态2 会员直连 / ①b 态3 连通失败）。 */
type MemberStage = "waiting" | "not_subscribed" | "member" | "test_failed";

const CUSTOM_PROVIDER_ID = "custom";
/** 订阅页（契约 §0：落地页 origin 白名单含 accounts）。①b 态1 一键回订阅页。 */
const MEMBER_SUBSCRIBE_URL = "https://accounts.gearclickist.com/pay";
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

function authModeLabel(mode: ProviderAuthMode): string {
  if (mode === "api_key") return "API Key";
  if (mode === "oauth") return "OAuth / 设备码";
  return "环境凭据";
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
  const [customBaseUrl, setCustomBaseUrl] = useState("");
  const [customModel, setCustomModel] = useState("");
  const [customProtocolNeedsChoice, setCustomProtocolNeedsChoice] = useState(false);
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
  // ── 会员流（①a/①b）──────────────────────────────────────────────────────
  const memberSelected = providerId !== "" && isMemberWizardType(providerId) && !custom;
  const [memberStage, setMemberStage] = useState<MemberStage>("waiting");
  const [memberMe, setMemberMe] = useState<MemberMe | null>(null);
  const [memberMessage, setMemberMessage] = useState("");
  const [memberBusy, setMemberBusy] = useState(false);
  const [memberLoginUrl, setMemberLoginUrl] = useState("");
  const providerMenuRef = useRef<HTMLDivElement>(null);
  const protocolMenuRef = useRef<HTMLDivElement>(null);
  const modelMenuRef = useRef<HTMLDivElement>(null);

  const customDiscovery = useCustomModelDiscovery({
    baseUrl: customBaseUrl,
    apiKey,
    enabled: custom && !customProtocolNeedsChoice,
    discover: discoverCustomProviderModels,
  });
  const {
    models: customModels,
    state: customModelState,
    message: customModelMessage,
    error: customModelError,
    protocolConfirmed: customProtocolConfirmed,
    kind: customKind,
  } = customDiscovery;

  const selectedProvider = useMemo(
    () => providers.find((provider) => provider.provider_id === providerId),
    [providerId, providers],
  );

  useEffect(() => {
    setDesktop(isDesktopRuntime());
  }, []);

  /**
   * 换票第一步 + 打开系统浏览器（契约 §3.1/§3.2）：device/start → login_url →
   * 系统浏览器。客户端停留等待页，只等 deep-link，不轮询。
   */
  const startMemberFlow = useCallback(async (): Promise<void> => {
    setMemberStage("waiting");
    setMemberMessage("");
    if (!isDesktopRuntime()) {
      setMemberMessage("浏览器预览不能唤起系统浏览器与接收 deep-link，请在桌面版中完成登录。");
      return;
    }
    const result = await startMemberLogin();
    if (!result.ok) {
      setMemberMessage(result.message);
      return;
    }
    setMemberLoginUrl(result.login_url);
    try {
      await openExternalUrl(result.login_url);
    } catch {
      setMemberMessage("没能打开系统浏览器，可点下方「重新打开浏览器页面」。");
    }
  }, []);

  /** 「重新打开浏览器页面」：复用已起的 login_url；没有则重起一轮 device_code。 */
  const reopenMemberBrowser = useCallback(async (): Promise<void> => {
    if (memberLoginUrl) {
      try {
        await openExternalUrl(memberLoginUrl);
        return;
      } catch {
        /* 落到重起一轮 */
      }
    }
    await startMemberFlow();
  }, [memberLoginUrl, startMemberFlow]);

  /** 收到 deep-link / 手动重试后的收口：刷新会员状态并决定中间态（①b）。 */
  const syncMemberState = useCallback(async (): Promise<MemberMe | null> => {
    const status = await fetchMemberStatus().catch(() => null);
    if (!status || !status.ok || !status.logged_in) {
      setMemberMe(null);
      return null;
    }
    setMemberMe(status.me);
    if (status.me.member) {
      setMemberStage("member");
      return status.me;
    }
    // 已登录但无有效订阅（态1）；本地已有 JWT 时也走这里（换机/重装快路径）。
    setMemberStage("not_subscribed");
    return status.me;
  }, []);

  /** 连通测试（铁律⑤ 的放行门）：态2 直连路径也要过这一关。 */
  const probeMemberConnection = useCallback(async (): Promise<boolean> => {
    try {
      const { testMemberConnection } = await import("@/lib/api");
      const result = await testMemberConnection();
      if (result.ok) {
        setMemberStage("member");
        setMemberMessage("");
        return true;
      }
      setMemberStage("test_failed");
      setMemberMessage(result.message);
      return false;
    } catch {
      setMemberStage("test_failed");
      setMemberMessage("连接测试未能发起，请稍后重试。");
      return false;
    }
  }, []);

  // 选中会员档即起流（线框 ① 注：选中后按钮变「登录并订阅」→ 打开系统浏览器）。
  useEffect(() => {
    if (!memberSelected) return;
    void startMemberFlow();
  }, [memberSelected, startMemberFlow]);

  // deep-link 监听：只在会员流内消费；scene 无关，失败一律静默（§3.3-7）。
  const handledUrlsRef = useRef(new Set<string>());
  useEffect(() => {
    if (!memberSelected || !desktop) return undefined;
    let disposed = false;
    let unlisten: (() => void) | null = null;

    const process = async (urls: string[] | null) => {
      const fresh = (urls ?? []).filter((url) => !handledUrlsRef.current.has(url));
      for (const url of fresh) handledUrlsRef.current.add(url);
      if (!fresh.length) return;
      const link = (await import("@/lib/member")).firstMemberDeepLink(fresh);
      if (!link) return;
      if (link.scene === "open" || !link.ticket || !link.dc) {
        // §3.2 触发 2：无 ticket 的第二次跳转（支付成功唤醒）不报错，只用已有
        // 凭证刷新；会员态变化照常推进中间态。
        await syncMemberState();
        return;
      }
      setMemberBusy(true);
      try {
        const result = await exchangeMemberTicket({ ticket: link.ticket, dc: link.dc });
        if (disposed) return;
        if (!result.ok) {
          // dc 不匹配 / ticket 过期 / 已消费：都降级为「用已有凭证刷新」。
          await syncMemberState();
          return;
        }
        if (result.connection_ok === false) {
          const me = await syncMemberState();
          if (me?.member) {
            setMemberStage("test_failed");
            setMemberMessage(result.connection_message ?? "连接测试未通过，可重试。");
          }
          return;
        }
        const me = await syncMemberState();
        if (me?.member) setMemberStage("member");
      } finally {
        if (!disposed) setMemberBusy(false);
      }
    };

    void (async () => {
      try {
        const { getCurrent, onOpenUrl } = await import("@tauri-apps/plugin-deep-link");
        if (disposed) return;
        unlisten = await onOpenUrl((urls) => {
          void process(urls);
        });
        if (disposed) {
          unlisten();
          unlisten = null;
          return;
        }
        await process(await getCurrent());
      } catch {
        // 插件不可用：不影响其余路径（手动重开、BYOK）。
      }
    })();

    return () => {
      disposed = true;
      unlisten?.();
    };
  }, [memberSelected, desktop, syncMemberState]);

  // 已存会员档（换机/重装）直接进 ①b 态2：先取一次会员态，再连通测试。
  useEffect(() => {
    if (!memberSelected || !desktop) return;
    void (async () => {
      const me = await syncMemberState();
      if (me?.member) await probeMemberConnection();
    })();
    // 只在选中会员档时跑一次；deep-link 到达由上面的监听推进。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [memberSelected, desktop]);

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
            customDiscovery.confirmProtocol(profile.kind);
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
    setAuthMode(firstAuthMode(selectedProvider.auth_modes));
  }, [selectedProvider]);

  useEffect(() => {
    if (customDiscovery.needsProtocolChoice && !customProtocolNeedsChoice) {
      setCustomProtocolNeedsChoice(true);
    }
  }, [customDiscovery.needsProtocolChoice, customProtocolNeedsChoice]);

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
      void getProviderAuthOperation(operation.id)
        .then(async (next) => {
          setOperation(next);
          if (next.status === "succeeded" && profileId !== null) {
            setConnectionState("testing");
            await takeProviderAuthResult(profileId, next.id);
            const status = await testProviderProfile(profileId);
            setConnectionState(status.status === "ready" ? "ready" : "failed");
            setMessage(status.status === "ready" ? `连接成功 · ${custom ? customModel : selectedModelLabel}` : status.message);
          } else if (["failed", "cancelled", "timed_out"].includes(next.status)) {
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
      const profileInput = custom
        ? {
            name: "自定义 Provider",
            kind: customKind,
            base_url: customBaseUrl,
            model_id: customModel,
            context_window: selectedCustomModel?.context_window ?? null,
            max_tokens: selectedCustomModel?.max_tokens ?? null,
            api_key: apiKey,
            is_default: true,
          } as const
        : {
            name: selectedProvider?.provider_name ?? providerId,
            kind: "builtin",
            provider_id: providerId,
            model_id: modelId,
            api_key: authMode === "api_key" ? apiKey : undefined,
            is_default: true,
          } as const;
      const profile = profileId === null
        ? await createProviderProfile(profileInput)
        : await updateProviderProfile(profileId, profileInput);
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
    } catch {
      setConnectionState("failed");
      setMessage("连接失败。请检查 Provider、模型和认证信息后重试。");
    }
  };

  const submitPrompt = async () => {
    const prompt = operation?.prompts[0];
    if (!operation || !prompt || !promptValue) return;
    try {
      const next = await submitProviderAuthInput(operation.id, prompt.prompt_id, promptValue);
      setPromptValue("");
      setOperation(next);
    } catch {
      setMessage("认证输入未被接受，请重试。");
    }
  };

  const finish = async () => {
    setFinishing(true);
    setMessage("");
    try {
      if (!desktop || !captureOptIn) throw new Error("capture_required");
      const coordinator = await setDesktopCaptureEnabled(true);
      const status = await getCaptureStatus();
      if (
        !coordinator.enabled
        || status.availability !== "available"
        || !status.platform_supported
        || status.raw_input_permission === "denied"
        || !status.capture_enabled
        || status.runtime_health === "unavailable"
      ) throw new Error("capture_not_ready");
      const state = await completeOnboarding("connected");
      if (state.availability !== "available") throw new Error("unavailable");
      router.push("/");
    } catch {
      setMessage("设置未能完整保存。没有假装自动采集已启用，请重试。");
      setFinishing(false);
    }
  };

  const selectedModel = selectedProvider?.models.find((model) => model.model_id === modelId);
  const selectedCustomModel = customModels.find((model) => model.model_id === customModel);
  const selectedModelLabel = selectedModel?.model_name ?? selectedModel?.model_id ?? modelId;
  const connectionReady = connectionState === "ready";
  // ①b 态2（老会员直连）：会员态 + 连通测试都过才放行（铁律⑤）。
  const memberReady = memberSelected && memberStage === "member" && connectionState !== "failed";
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
    setProfileId(null);
    setSavedProfile(null);
    setMessage("");
    setConnectionState("idle");
    if (nextProviderId === CUSTOM_PROVIDER_ID) {
      setCustom(true);
      setProviderId("");
      setAuthMode("api_key");
      setCustomModel("");
      setCustomProtocolNeedsChoice(false);
      customDiscovery.reset();
      return;
    }
    const nextProvider = providers.find((provider) => provider.provider_id === nextProviderId);
    setCustom(false);
    setProviderId(nextProviderId);
    setAuthMode(firstAuthMode(nextProvider?.auth_modes));
  };

  const selectModel = (nextModelId: string) => {
    setModelId(nextModelId);
    setCustomModel(nextModelId);
    setOpenMenu(null);
    setMessage("");
    setConnectionState("idle");
  };

  const updateCustomConnection = (field: "baseUrl" | "apiKey", value: string) => {
    if (field === "baseUrl") setCustomBaseUrl(value);
    else setApiKey(value);
    setCustomModel("");
    setCustomProtocolNeedsChoice(false);
    customDiscovery.reset();
  };

  const selectCustomProtocol = (nextKind: CustomProviderKind) => {
    customDiscovery.confirmProtocol(nextKind);
    setOpenMenu(null);
  };

  const useManualCustomModel = () => {
    setOpenMenu(null);
    setCustomModel("");
    customDiscovery.enterManualMode();
  };

  // 档位显示名（会员档强制线框文案；其余沿用目录名）。
  const wizardTypeLabels = useMemo(() => {
    const map = new Map<string, string>();
    for (const option of wizardTypeOptions({ schema_version: "coach_provider_catalog.v1", providers })) {
      map.set(option.id, option.label);
    }
    return map;
  }, [providers]);

  return (
    <main className="task3-onboarding" id="main-content">
      <div
        className="task3-onboarding-brand"
        onMouseDown={(event) => {
          if (event.button === 0) void startWindowDragging();
        }}
      >
        <span>Aiming Cookie</span>
        <div className="task3-toolbar-spacer" />
        <TauriWindowControls />
      </div>
      <div className="task3-onboarding-progress" aria-label={`第 ${step} 步，共 2 步`}>
        <span data-active={step === 1 || undefined}>1</span>
        <i />
        <span data-active={step === 2 || undefined}>2</span>
      </div>

      {step === 1 && memberSelected ? (
        <MemberConnect
          busy={memberBusy}
          email={memberMe?.user.email ?? null}
          message={memberMessage}
          onContinue={() => setStep(2)}
          onReopen={() => void reopenMemberBrowser()}
          onRetry={() => void (async () => {
            setMemberBusy(true);
            const me = await syncMemberState();
            if (me?.member) await probeMemberConnection();
            setMemberBusy(false);
          })()}
          onSubscribe={() => void openExternalUrl(MEMBER_SUBSCRIBE_URL)}
          onUseByok={() => selectProvider(CUSTOM_PROVIDER_ID)}
          stage={memberStage}
        />
      ) : step === 1 ? (
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
                    <span aria-live="polite">{custom ? "自定义 Provider" : (wizardTypeLabels.get(providerId) ?? selectedProvider?.provider_name ?? "选择 Provider")}</span>
                  </button>
                  {openMenu === "provider" ? (
                    <div aria-label="Provider 选项" className="task3-onboarding-dropdown-menu" id="onboarding-provider-listbox" role="listbox">
                      {providers
                        .filter((provider) => !isMemberWizardType(provider.provider_id))
                        .map((provider) => (
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
                      {/* 会员档置顶（线框 ①）：账号订阅，登录即用。 */}
                      {providers.some((provider) => isMemberWizardType(provider.provider_id)) ? (
                        <button
                          aria-selected={!custom && isMemberWizardType(providerId)}
                          className="task3-onboarding-dropdown-option"
                          data-member="true"
                          onClick={() => selectProvider("aiming-cookie-relay")}
                          role="option"
                          type="button"
                        >
                          <span>{MEMBER_COPY.providerDropdownLabel}</span>
                          <small>{MEMBER_COPY.providerDropdownHint}</small>
                        </button>
                      ) : null}
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
                                  aria-selected={candidate.model_id === customModel}
                                  className="task3-onboarding-dropdown-option"
                                  key={candidate.model_id}
                                  onClick={() => selectModel(candidate.model_id)}
                                  role="option"
                                  type="button"
                                >
                                  <span>{candidate.model_id}</span>
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
                  {event.type === "auth_url" ? <a href={event.url} rel="noreferrer" target="_blank" onClick={(e) => handleExternalClick(e, event.url)}>打开 Provider 授权页</a> : null}
                  {event.type === "device_code" ? <p>设备码：<strong>{event.user_code}</strong> · <a href={event.verification_uri} rel="noreferrer" target="_blank" onClick={(e) => handleExternalClick(e, event.verification_uri)}>前往验证</a></p> : null}
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
            <div />
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
      ) : (
        <section className="task3-onboarding-sheet task3-onboarding-step" aria-labelledby="capture-title" key="capture">
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
            <Button disabled={finishing || !desktop || !captureOptIn || !(connectionReady || memberReady)} onClick={() => void finish()}>{finishing ? "正在保存" : "进入工作台"}</Button>
          </div>
        </section>
      )}
    </main>
  );
}

/**
 * 会员连接流（线框 ①a/①b）：同一张 wizard 卡按 stage 原位切换。
 * 卡位固定，不动布局；等待页只做展示、不报错（契约 §3.3-7）。
 */
function MemberConnect({
  stage,
  email,
  busy,
  message,
  onContinue,
  onReopen,
  onRetry,
  onSubscribe,
  onUseByok,
}: {
  stage: MemberStage;
  email: string | null;
  busy: boolean;
  message: string;
  onContinue: () => void;
  onReopen: () => void;
  onRetry: () => void;
  onSubscribe: () => void;
  onUseByok: () => void;
}) {
  return (
    <section className="task3-onboarding-sheet task3-onboarding-step task3-member-connect" aria-labelledby="member-title" key="member">
      <h1 id="member-title">连接模型服务 · Aiming Cookie</h1>

      {stage === "waiting" ? (
        <>
          <div className="task3-member-card" data-tone="waiting">
            <div aria-hidden="true" className="task3-member-glyph">🌐</div>
            <strong>{MEMBER_COPY.waitingTitle}</strong>
            <p>
              {MEMBER_COPY.waitingBody}
              <br />
              {MEMBER_COPY.waitingReopenHint}
              <button className="task3-member-link" onClick={onReopen} type="button">点此重新打开</button>
            </p>
          </div>
          <Button disabled={busy} onClick={onReopen} variant="secondary">{MEMBER_COPY.reopenBrowser}</Button>
        </>
      ) : null}

      {stage === "not_subscribed" ? (
        <>
          <div className="task3-member-card" data-tone="account">
            <div aria-hidden="true" className="task3-member-glyph">👤</div>
            <strong>
              {MEMBER_COPY.notSubscribedPrefix}
              {email ? ` ${maskEmail(email)}` : ""} · {MEMBER_COPY.notSubscribedSuffix}
            </strong>
            <p>
              {MEMBER_COPY.notSubscribedBody}
              <br />
              {MEMBER_COPY.notSubscribedBodyLine2}
            </p>
          </div>
          <Button className="task3-member-primary" onClick={onSubscribe} variant="primary">{MEMBER_COPY.openSubscribePage}</Button>
          <div className="task3-member-secondary-actions">
            <Button disabled={busy} onClick={onReopen} variant="secondary">{MEMBER_COPY.reopenBrowser}</Button>
          </div>
          <Button onClick={onUseByok} variant="ghost">{MEMBER_COPY.useByok}</Button>
        </>
      ) : null}

      {stage === "member" ? (
        <>
          <div className="task3-member-card" data-tone="member">
            <div aria-hidden="true" className="task3-member-glyph">✅</div>
            <strong>{MEMBER_COPY.alreadyMember}</strong>
            <p>
              {MEMBER_COPY.alreadyMemberBody}
              <br />
              {MEMBER_COPY.alreadyMemberBodyLine2}
            </p>
          </div>
          <p className="task3-member-ok">● {MEMBER_COPY.connected}</p>
          <Button className="task3-member-primary" onClick={onContinue} variant="primary">{MEMBER_COPY.continueLabel}</Button>
        </>
      ) : null}

      {stage === "test_failed" ? (
        <>
          <div className="task3-member-card" data-tone="error">
            <div aria-hidden="true" className="task3-member-glyph">⚠️</div>
            <strong data-tone="error">{MEMBER_COPY.testFailed}</strong>
            <p>
              {message || MEMBER_COPY.testFailedBody}
              <br />
              {MEMBER_COPY.testFailedBodyLine2}
            </p>
          </div>
          <Button className="task3-member-primary" disabled={busy} onClick={onRetry} variant="primary">{MEMBER_COPY.retryConnect}</Button>
          <Button disabled={busy} onClick={onReopen} variant="secondary">{MEMBER_COPY.reopenBrowser}</Button>
          <Button onClick={onUseByok} variant="ghost">{MEMBER_COPY.useByok}</Button>
        </>
      ) : null}

      {/* 退出会员流（①a/①b 的「改用自定义 Provider（BYOK）」）：清理已存会员
          凭据不必——凭据是会员资产，退出流不等于退出登录。 */}
      {stage === "waiting" ? <Button onClick={onUseByok} variant="ghost">{MEMBER_COPY.useByok}</Button> : null}
      {message && stage === "waiting" ? <Notice tone="warning">{message}</Notice> : null}
    </section>
  );
}
