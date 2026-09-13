"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import {
  authorizeProviderProfile,
  cancelProviderAuthOperation,
  createProviderProfile,
  deleteProviderProfile,
  discoverCustomProviderModels,
  getProviderAuthOperation,
  getOfficialRelayBalance,
  getProviderCatalog,
  getProviderCredential,
  listStoredCustomProviderModels,
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
  isOfficialRelayProfile,
  OFFICIAL_RELAY_PROVIDER_ID,
  useCustomModelDiscovery,
} from "@/lib/provider-helpers";
import {
  WIZARD_STEP_COUNT,
  buildWizardPayload,
  buildWizardProbePayload,
  emptyWizardDraft,
  isWizardCustom,
  previewBuiltinRequestUrl,
  previewCustomRequestUrl,
  wizardCatalogProvider,
  wizardCheckFingerprint,
  wizardDefaultName,
  wizardNameConflicts,
  wizardTypeOptions,
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
import { IconEye, IconEyeOff, IconPencil, IconTrash } from "@/ui/icons";
import { Button, Dialog, Field, FieldControl, Loading, Notice, Panel, Status } from "@/ui/primitives";


/** 授权页等外链：桌面端 opener 唤默认浏览器（WebView2 拦 _blank）。 */
function handleExternalClick(event: { preventDefault(): void }, url: string): void {
  void import("@/lib/desktop").then(({ isDesktopRuntime, openExternalUrl }) => {
    if (isDesktopRuntime()) {
      event.preventDefault();
      void openExternalUrl(url);
    }
  });
}

// Raycast 式先验后存：干跑结果绑定提交时的表单指纹，
// 表单任何变动都会让旧结论失效并回到未验证态。
type DraftCheck =
  | { phase: "idle" }
  | { phase: "checking"; fingerprint: string }
  | { phase: "done"; fingerprint: string; passed: boolean; message: string };

type WizardStep = 1 | 2;

type ConfirmAction = {
  title: string;
  impact: string;
  run: () => Promise<void>;
} | null;

const WIZARD_STEP_LABELS = ["选类型", "名称与凭据"] as const;

/** 官方档未建档时的合成档案（0911 拍板：未连接也可选中查看详情、可填 Key）。 */
const SYNTHETIC_OFFICIAL_PROFILE: ProviderProfile = {
  id: -1,
  name: "Aiming Cookie 官方",
  provider_id: OFFICIAL_RELAY_PROVIDER_ID,
  kind: "builtin",
  base_url: null,
  model_id: "",
  is_default: false,
  configured: false,
  credential_configured: false,
  has_api_key: false,
  status: "unconfigured",
};

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
  }
}

function providerTypeLabel(profile: ProviderProfile, catalog: ProviderCatalogV1 | null): string {
  if (isCustomProviderKind(profile.kind)) {
    return profile.kind === "custom_anthropic_compatible" ? "Anthropic 兼容" : "OpenAI 兼容";
  }
  const entry = catalog?.providers.find((provider) => provider.provider_id === profile.provider_id);
  return entry?.provider_name ?? profile.provider_id ?? "内置 Provider";
}

/** 测活时间的人话相对时长（线框：上次测活成功 · N 小时前）。 */
function relativeTime(value: string | null | undefined): string {
  if (!value) return "";
  const time = new Date(value).getTime();
  if (Number.isNaN(time)) return "";
  const minutes = Math.max(0, Math.round((Date.now() - time) / 60_000));
  if (minutes < 1) return "刚刚";
  if (minutes < 60) return `${minutes} 分钟前`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} 小时前`;
  return `${Math.floor(hours / 24)} 天前`;
}

/** 第 1 步「下一步」的门槛：所选内置类型须在目录可用（自定义恒可用）。
 * 第 2 步的门槛在按钮自身：「测试」要求凭据/端点齐备，「完成」要求 payload
 * 齐备（含测试成功后选定的模型）。 */
function wizardStepReady(step: WizardStep, draft: WizardDraft, builtinAvailable: boolean): boolean {
  if (step === 1) return isWizardCustom(draft.typeId) || builtinAvailable;
  return true;
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
  // 官方档（Aiming Cookie 官方）是常驻内置条目：不依赖用户档案存在，永远置顶可选。
  const [officialSelected, setOfficialSelected] = useState(false);
  const relayArchive = useMemo(
    () => profiles.find((profile) => isOfficialRelayProfile(profile)) ?? null,
    [profiles],
  );
  const selectedProfile = useMemo(
    () => profiles.find((profile) => profile.id === selectedId) ?? activeProfile,
    [activeProfile, profiles, selectedId],
  );

  const [confirmAction, setConfirmAction] = useState<ConfirmAction>(null);
  const [switchingProvider, setSwitchingProvider] = useState(false);
  const [nameDraft, setNameDraft] = useState<string | null>(null);
  const [baseUrlDraft, setBaseUrlDraft] = useState<string | null>(null);
  const [credentialDraft, setCredentialDraft] = useState("");
  // Key 掩码行编辑态（点点 0912：点 Key 文字进入粘贴，回车/失焦即存，Esc 取消）。
  const [keyEditing, setKeyEditing] = useState(false);
  // 眼睛的「显示 Key」（点点 0912 拍板）：点眼睛拉取存档明文切换显示。
  const [revealedKey, setRevealedKey] = useState<{ id: number; key: string } | null>(null);
  const [testingConnection, setTestingConnection] = useState(false);
  const [lastTest, setLastTest] = useState<{ passed: boolean; message: string } | null>(null);
  // 官方档（aiming-cookie-relay）的计费方式视图：会员计划 / API 计费。
  const [billingMode, setBillingMode] = useState<"plan" | "api">("plan");
  const [billingMenuOpen, setBillingMenuOpen] = useState(false);
  const billingMenuRef = useRef<HTMLDivElement | null>(null);

  // 官方档余额（0912 线框拍板）：打开 API 计费视图自动拉一次，「刷新余额」手动重拉。
  const [relayBalance, setRelayBalance] = useState<
    { phase: "idle" | "loading" | "ready" | "error"; value: number | null; message: string | null }
  >({ phase: "idle", value: null, message: null });
  // 已存自定义档详情的模型发现（点「获取模型」后才有内容；内置档走目录刷新）。
  const [detailModels, setDetailModels] = useState<
    { phase: "idle" | "loading" | "ready" | "error"; models: string[]; message: string | null }
  >({ phase: "idle", models: [], message: null });
  // 内置档详情「获取模型」：重新拉取 sidecar 目录快照。
  const [detailCatalogReload, setDetailCatalogReload] = useState<ProviderCatalogV1 | null>(null);
  const [detailCatalogReloading, setDetailCatalogReloading] = useState(false);

  const loadRelayBalance = () => {
    setRelayBalance((current) => ({ phase: "loading", value: current.value, message: null }));
    void getOfficialRelayBalance()
      .then((next) => setRelayBalance({ phase: "ready", value: next.balance, message: null }))
      .catch((error: unknown) => {
        // apiError 约定：状态码编码在 err.name（ApiError_401）。
        const name = error instanceof Error ? error.name : "";
        const badKey = name === "ApiError_401" || name === "ApiError_403";
        setRelayBalance((current) => ({
          phase: "error",
          value: current.value,
          message: badKey ? "API Key 无效或已被重置" : "余额暂时无法读取，请稍后重试。",
        }));
      });
  };

  // 打开 API 计费视图且已存 Key 时自动拉一次（幂等：仅在 idle 态触发）。
  useEffect(() => {
    if (!officialSelected || billingMode !== "api" || !relayArchive?.credential_configured) return;
    if (relayBalance.phase !== "idle") return;
    loadRelayBalance();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [officialSelected, billingMode, relayArchive?.credential_configured, relayBalance.phase]);

  /** 已存自定义档「获取模型」：只传 profile_id，key 留在 sidecar 就地发现并
   *  更新存档列表（点点 0912 拍板）；成功后刷新投影，下次直接显示。 */
  const loadDetailModels = (profile: ProviderProfile) => {
    setDetailModels({ phase: "loading", models: [], message: null });
    void listStoredCustomProviderModels(profile.id)
      .then(async (next) => {
        setDetailModels(next.models.length
          ? { phase: "ready", models: next.models.map((model) => model.model_id), message: null }
          : { phase: "error", models: [], message: "没有读取到可用模型" });
        await refresh(true);
      })
      .catch(() => setDetailModels({ phase: "error", models: [], message: "连接失败，请检查设置" }));
  };

  // ── 添加向导状态（模态两步：①选类型 ②名称与凭据 + 测试/完成） ──
  const [wizardOpen, setWizardOpen] = useState(false);
  const [wizardStep, setWizardStep] = useState<WizardStep>(1);
  const [wizardDraft, setWizardDraft] = useState<WizardDraft>(emptyWizardDraft);
  const [wizardShowKey, setWizardShowKey] = useState(false);
  const [wizardCheck, setWizardCheck] = useState<DraftCheck>({ phase: "idle" });
  const wizardCheckAbort = useRef<AbortController | null>(null);
  // 内置类型「获取模型」：从 sidecar 重新拉取目录（覆盖父级传入的快照）。
  const [wizardCatalogReload, setWizardCatalogReload] = useState<ProviderCatalogV1 | null>(null);
  const [wizardCatalogReloading, setWizardCatalogReloading] = useState(false);

  // ── OAuth 授权流程（已有档，先保存后授权） ───────────────────
  const [authOperation, setAuthOperation] = useState<ProviderAuthOperation | null>(null);
  const [authProfileId, setAuthProfileId] = useState<number | null>(null);
  const [authPromptValue, setAuthPromptValue] = useState("");

  const wizardCustom = isWizardCustom(wizardDraft.typeId);
  const wizardCatalogSource = wizardCatalogReload ?? catalog;
  const wizardCatalogEntry = wizardCustom ? undefined : wizardCatalogProvider(wizardCatalogSource, wizardDraft.typeId);
  // 第 1 步类型卡：catalog 派生完整目录 + 自定义兜底（点点 0911 线框拍板）。
  const wizardTypeList = useMemo(() => wizardTypeOptions(wizardCatalogSource), [wizardCatalogSource]);
  const wizardFingerprint = wizardCheckFingerprint(wizardDraft);
  const wizardCheckingNow = wizardCheck.phase === "checking" && wizardCheck.fingerprint === wizardFingerprint;
  const wizardVerified = wizardCheck.phase === "done"
    && wizardCheck.passed
    && wizardCheck.fingerprint === wizardFingerprint;

  const customDiscovery = useCustomModelDiscovery({
    // 模型列表在测试成功后才获取/展示（点点 0911 拍板）；协议确认结论
    // customKind 由端点+Key 派生，不参与连通指纹（否则确认即解锁死循环）。
    baseUrl: wizardDraft.baseUrl,
    apiKey: wizardDraft.apiKey,
    enabled: wizardOpen && wizardCustom && wizardVerified,
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
  // 「测试」与「完成」各用一份候选 payload：测试走免模型连通探测（内置不选
  // 模型也能先测连），完成时校验并写入带模型的完整档案。
  const wizardProbePayload: ProviderProfileCreate | null = buildWizardProbePayload(wizardDraft, {
    isCustom: wizardCustom,
    customKind,
    builtinProviderAvailable: Boolean(wizardCatalogEntry),
    defaultName: wizardDefaultName(wizardCatalogSource, wizardDraft.typeId),
  });
  const wizardPayload: ProviderProfileCreate | null = buildWizardPayload(wizardDraft, {
    isCustom: wizardCustom,
    customKind,
    selectedCustomModel,
    builtinModelIsReasoning: wizardModelIsReasoning,
    builtinProviderAvailable: Boolean(wizardCatalogEntry),
    defaultName: wizardDefaultName(wizardCatalogSource, wizardDraft.typeId),
    isFirstProfile: profiles.length === 0,
  });
  const wizardNameConflict = wizardNameConflicts(
    wizardDraft.name,
    profiles.map((profile) => profile.name),
  );

  // 表单任何变动都会改变指纹：中止在途检查，回到未验证态并锁住完成。
  useEffect(() => {
    if (wizardCheck.phase === "checking" && wizardCheck.fingerprint !== wizardFingerprint) {
      wizardCheckAbort.current?.abort();
    }
  }, [wizardCheck, wizardFingerprint]);

  useEffect(() => () => wizardCheckAbort.current?.abort(), []);

  // 计费方式下拉（官方档）：外点即收起（CoachModelMenu 同款 mousedown 惯例）。
  useEffect(() => {
    if (!billingMenuOpen) return;
    const onPointerDown = (event: MouseEvent) => {
      if (event.target instanceof Node && billingMenuRef.current?.contains(event.target)) return;
      setBillingMenuOpen(false);
    };
    document.addEventListener("mousedown", onPointerDown);
    return () => document.removeEventListener("mousedown", onPointerDown);
  }, [billingMenuOpen]);

  const openWizard = () => {
    setWizardDraft(emptyWizardDraft());
    setWizardStep(1);
    setWizardShowKey(false);
    setWizardCheck({ phase: "idle" });
    setWizardCatalogReload(null);
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
    // 冻结本次检查对应的探测候选与指纹：期间端点/Key 再变，结论也不解锁完成。
    const payload = wizardProbePayload;
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
          : `${status.message}。请核对 API Key 与端点后重试。`,
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
    setWizardOpen(false);
    // 模型发现存档播种（点点 0912 拍板）：新档立即可见模型列表，无需再点获取。
    if (isCustomProviderKind(created.kind)) {
      void listStoredCustomProviderModels(created.id).catch(() => undefined);
    }
    await refresh(true);
  };

  const reloadWizardCatalog = () => {
    setWizardCatalogReloading(true);
    void getProviderCatalog()
      .then((next) => setWizardCatalogReload(next))
      .catch(() => notify("模型目录暂时无法刷新，请稍后重试。"))
      .finally(() => setWizardCatalogReloading(false));
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

  /** 自定义档 Base URL 编辑（线框：Base URL 行自定义可编辑，内置只读端点）。 */
  const saveBaseUrl = async (profile: ProviderProfile) => {
    const nextBaseUrl = baseUrlDraft?.trim();
    if (!nextBaseUrl || nextBaseUrl === (profile.base_url ?? "")) return;
    try {
      // PUT 是整档更新；不带新 api_key 时 sidecar 保留现有 credential。
      await updateProviderProfile(profile.id, {
        name: profile.name,
        kind: profile.kind,
        provider_id: profile.provider_id || null,
        base_url: nextBaseUrl,
        model_id: profile.model_id,
        reasoning_effort: profile.reasoning_effort ?? null,
        context_window: profile.context_window ?? null,
        max_tokens: profile.max_tokens ?? null,
        api_key: null,
        is_default: profile.is_default,
      });
      setBaseUrlDraft(null);
      await refresh(true);
    } catch {
      notify("Base URL 未能保存，请重试。");
    }
  };

  /** 换 Key（点点 0912 拍板：粘贴完回车/失焦即存，无按钮、无确认弹窗）。
   *  官方档未建档时（合成档案 id=-1），保存 Key 即创建 relay 档（0911 拍板）。 */
  const saveNewCredential = async (profile: ProviderProfile) => {
    const key = credentialDraft;
    if (!key) return;
    try {
      if (profile.id < 0) {
        await createProviderProfile({
          name: "Aiming Cookie 官方",
          kind: "builtin",
          provider_id: OFFICIAL_RELAY_PROVIDER_ID,
          model_id: detailCatalogEntry?.models[0]?.model_id ?? "deepseek-v4-flash",
          api_key: key,
          is_default: false,
        });
      } else {
        await setProviderApiKey(profile.id, key);
      }
      setCredentialDraft("");
      setKeyEditing(false);
      setRevealedKey(null);
      notify("API Key 已保存。");
      await refresh(true);
    } catch {
      notify("API Key 未能保存，请重试。");
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

  const selectedAuthModes = officialSelected
    ? catalog?.providers.find((provider) => provider.provider_id === OFFICIAL_RELAY_PROVIDER_ID)?.auth_modes
      ?? ["api_key" as const]
    : selectedProfile
      ? catalog?.providers.find((provider) => provider.provider_id === selectedProfile.provider_id)?.auth_modes
        ?? (isCustomProviderKind(selectedProfile.kind) ? ["api_key" as const] : [])
      : [];

  const wizardModelOptions = wizardCustom
    ? customModels.map((model) => ({ id: model.model_id, label: model.model_id }))
    : (wizardCatalogEntry?.models ?? []).map((model) => ({ id: model.model_id, label: model.model_name ?? model.model_id }));
  const wizardBasePreview = wizardCustom
    ? previewCustomRequestUrl(customKind, wizardDraft.baseUrl)
    : previewBuiltinRequestUrl(wizardCatalogSource, wizardDraft.typeId);

  // 0911 点点：官方档常驻置顶，未连接（无档案）也可选中查看详情（登录/填 Key）。
  // 无存档时用合成档案渲染完整官方详情（0912 线框：官方档永远有详情）。
  const detail = officialSelected ? (relayArchive ?? SYNTHETIC_OFFICIAL_PROFILE) : selectedProfile;
  const lastKeeper = profiles.length <= 1;
  // 官方档（线框 B 形态）：无 Base URL/API Key 常规连接行，走计费/套餐模板。
  const officialDetail = officialSelected || (detail ? isOfficialRelayProfile(detail) : false);
  // 内置档详情「获取模型」刷新后的目录快照优先于父级传入的快照。
  const detailCatalogSource = detailCatalogReload ?? catalog;
  const detailCatalogEntry = officialSelected
    ? detailCatalogSource?.providers.find((provider) => provider.provider_id === OFFICIAL_RELAY_PROVIDER_ID)
    : detail && !isCustomProviderKind(detail.kind)
      ? detailCatalogSource?.providers.find((provider) => provider.provider_id === detail.provider_id)
      : undefined;

  const reloadDetailCatalog = () => {
    if (!detail || isCustomProviderKind(detail.kind)) return;
    setDetailCatalogReloading(true);
    void getProviderCatalog()
      .then((next) => setDetailCatalogReload(next))
      .catch(() => notify("模型目录暂时无法刷新，请稍后重试。"))
      .finally(() => setDetailCatalogReloading(false));
  };

  // Key 掩码行（0912 拍板两段语义）：眼睛=显示/隐藏存档明文 Key；点 Key 文字
  // =进入粘贴编辑（回车/失焦即存、Esc 取消）。非 api_key 档回退只读掩码文本。
  const renderKeyMaskRow = (profile: ProviderProfile) => {
    if (!selectedAuthModes.includes("api_key")) {
      return <span className="task6-mono">{profile.credential_configured ? "••••••••" : "未配置"}</span>;
    }
    const revealed = revealedKey?.id === profile.id ? revealedKey.key : null;
    if (!keyEditing) {
      return (
        <span className="task6-provider-keymask">
          <button
            className="task6-provider-keymask-text"
            data-revealed={revealed ? "true" : undefined}
            disabled={!profile.credential_configured && !revealed}
            onClick={() => setKeyEditing(true)}
            title="点击更换 Key"
            type="button"
          >
            {revealed ?? (profile.credential_configured ? "••••••••" : "未配置")}
          </button>
          <button
            aria-label={revealed ? "隐藏 Key" : "显示 Key"}
            className="task6-provider-mask-btn"
            onClick={() => {
              if (revealed) { setRevealedKey(null); return; }
              if (!profile.credential_configured) { setKeyEditing(true); return; }
              void getProviderCredential(profile.id)
                .then((next) => setRevealedKey({ id: profile.id, key: next.api_key }))
                .catch(() => notify("Key 暂时无法读取，请稍后重试。"));
            }}
            title={revealed ? "隐藏 Key" : "显示 Key"}
            type="button"
          >
            {revealed ? <IconEyeOff /> : <IconEye />}
          </button>
        </span>
      );
    }
    return (
      <span className="task6-provider-keymask is-editing">
        <FieldControl
          aria-label="粘贴新的 API Key"
          autoFocus
          autoComplete="off"
          className="task6-provider-keymask-input"
          onBlur={() => { if (credentialDraft) void saveNewCredential(profile); else setKeyEditing(false); }}
          onChange={(event) => setCredentialDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") void saveNewCredential(profile);
            if (event.key === "Escape") { setCredentialDraft(""); setKeyEditing(false); }
          }}
          placeholder="粘贴新 Key，回车保存"
          type="text"
          value={credentialDraft}
        />
      </span>
    );
  };

  return (
    <>
      {loading ? (
        <Loading>正在读取设置</Loading>
      ) : (
        <div className="task6-provider-master">
          {/* 0912 线框拍板：列表与详情是两张自包含卡，不再共裹一块大板。 */}
          <Panel className="task6-provider-list-card">
            <div aria-label="Provider 档案" className="task6-provider-list">
            {/* 官方档常驻置顶（0911 点点）：未连接也在首位；连接状态来自自动测活。 */}
            <button
              aria-current={officialSelected || undefined}
              className="task6-provider-list-item"
              data-official
              key="official-relay"
              onClick={() => {
                setOfficialSelected(true);
                setSelectedId(null);
                setCredentialDraft("");
                setKeyEditing(false);
                setRevealedKey(null);
                setBaseUrlDraft(null);
                setLastTest(null);
                setBillingMode("plan");
                setBillingMenuOpen(false);
              }}
              role="listitem"
              type="button"
            >
              <span className="task6-provider-list-text">
                <span className="task6-provider-list-name">Aiming Cookie 官方</span>
                <span className="task6-provider-list-type">内置</span>
              </span>
              <span
                aria-hidden="true"
                className="task6-provider-dot"
                data-ready={relayArchive?.status === "ready" || undefined}
                data-unconfigured={!relayArchive || undefined}
              />
            </button>
            {/* 官方存档由置顶行承载，不再重复渲染用户档案行（0912 去重）。 */}
            {profiles.filter((profile) => !isOfficialRelayProfile(profile)).map((profile) => (
              <button
                aria-current={!officialSelected && detail?.id === profile.id ? "true" : undefined}
                className="task6-provider-list-item"
                key={profile.id}
                onClick={() => {
                  setOfficialSelected(false);
                  setSelectedId(profile.id);
                  setCredentialDraft("");
                  setKeyEditing(false);
                  setRevealedKey(null);
                  setBaseUrlDraft(null);
                  setLastTest(null);
                  setDetailModels({ phase: "idle", models: [], message: null });
                  setDetailCatalogReload(null);
                  setBillingMode("plan");
                  setBillingMenuOpen(false);
                }}
                role="listitem"
                type="button"
              >
                <span className="task6-provider-list-text">
                  <span className="task6-provider-list-name">{profile.name}</span>
                  <span className="task6-provider-list-type">{isCustomProviderKind(profile.kind) ? "自定义" : "内置"}</span>
                </span>
                {/* 行尾绿点＝连接正常，红点＝探测不通（线框；数据来自自动测活）。 */}
                <span aria-hidden="true" className="task6-provider-dot" data-ready={profile.status === "ready"} />
              </button>
            ))}
            {profiles.length === 0 ? <p className="task6-muted">还没有 Provider 档案。</p> : null}
            <Button className="task6-provider-add" onClick={() => void openWizard()} variant="primary">
              + 添加服务
            </Button>
            </div>
          </Panel>

          <div className="task6-provider-detail-pane">
            {!detail ? (
              <div className="task6-provider-empty">
                <p className="task6-muted">在左侧选择一个档案查看详情，或点「+ 添加服务」。</p>
              </div>
            ) : (
              <Panel className="task6-provider-detail-card">
              <article className="task6-provider-detail" key={detail.id}>
                {officialDetail ? (
                  <>
                    <div className="task6-provider-head">
                      <span className="task6-provider-name-plain">Aiming Cookie 官方</span>
                      <span className="task6-provider-head-gap" />
                      {/* 设为当前收进卡右上角 ghost 小钮（点点 0912 拍板 b）。 */}
                      {relayArchive && !relayArchive.is_default ? (
                        <Button disabled={switchingProvider} onClick={() => void makeActive(relayArchive.id)} size="compact" variant="ghost">设为当前</Button>
                      ) : null}
                      <div className="task6-provider-billing" ref={billingMenuRef}>
                        <Button
                          aria-expanded={billingMenuOpen}
                          onClick={() => setBillingMenuOpen((open) => !open)}
                          size="compact"
                          variant="secondary"
                        >
                          {billingMode === "plan" ? "会员计划" : "API 计费"} ▾
                        </Button>
                        {billingMenuOpen ? (
                          <div className="task6-provider-billing-menu" role="menu">
                            <button
                              className="task6-provider-billing-item"
                              data-selected={billingMode === "plan"}
                              onClick={() => { setBillingMode("plan"); setBillingMenuOpen(false); }}
                              role="menuitem"
                              type="button"
                            >
                              会员计划
                            </button>
                            <button
                              className="task6-provider-billing-item"
                              data-selected={billingMode === "api"}
                              onClick={() => { setBillingMode("api"); setBillingMenuOpen(false); }}
                              role="menuitem"
                              type="button"
                            >
                              API 计费
                            </button>
                          </div>
                        ) : null}
                      </div>
                    </div>

                    {billingMode === "plan" ? (
                      <>
                        {/* 会员系统未上线（点点 0912 拍板）：完整线框 UI + 空态，
                            数值显示「—」，升级/管理/解绑禁用带提示，上线后接真数据。 */}
                        <div className="task6-provider-plan" data-placeholder="true">
                          <div className="task6-provider-plan-head">
                            <strong>AC 会员 Pro</strong>
                            <span className="task6-provider-head-gap" />
                            <Button disabled title="会员系统上线后开放" variant="secondary">升级</Button>
                          </div>
                          <p className="task6-provider-plan-meta">
                            到期 — · <span className="task6-provider-plan-link" title="会员系统上线后开放">管理</span> · <span className="task6-provider-plan-link" title="会员系统上线后开放">解绑</span>
                          </p>
                        </div>
                        <div className="task6-provider-quota-label">剩余额度</div>
                        <div className="task6-provider-quota" data-placeholder="true">
                          <div className="task6-provider-quota-head">本月剩余</div>
                          <div className="task6-provider-quota-value">
                            <b>—</b>
                            <span className="task6-provider-quota-sub">重置于 —</span>
                          </div>
                          <div className="task6-provider-quota-meter"><i style={{ width: "0%" }} /></div>
                        </div>
                      </>
                    ) : (
                      <>
                        <div className="task6-provider-conn">
                          <div className="task6-provider-conn-row">
                            <dt>API Key</dt>
                            <dd>{renderKeyMaskRow(relayArchive ?? SYNTHETIC_OFFICIAL_PROFILE)}</dd>
                          </div>
                          <div className="task6-provider-conn-row">
                            <dt>余额</dt>
                            <dd className="task6-provider-liveness">
                              <span className="task6-provider-balance">
                                {relayBalance.phase === "ready" && relayBalance.value !== null ? `¥ ${relayBalance.value.toFixed(2)}` : "¥ --"}
                              </span>
                              {relayBalance.phase === "error" ? <span className="task6-provider-balance-error">{relayBalance.message}</span> : null}
                              <Button
                                className="task6-provider-liveness-btn"
                                disabled={!relayArchive?.credential_configured || relayBalance.phase === "loading"}
                                onClick={loadRelayBalance}
                                title={relayArchive?.credential_configured ? undefined : "填写 API Key 后可查余额"}
                                variant="secondary"
                              >
                                刷新余额
                              </Button>
                            </dd>
                          </div>
                        </div>
                      </>
                    )}

                    <div className="task6-provider-models">
                      <div className="task6-provider-models-title">模型列表</div>
                      <div className="task6-provider-models-list">
                        {(detailCatalogEntry?.models ?? []).map((model) => (
                          <div className="task6-provider-model-row" key={model.model_id}>
                            <span>{model.model_name ?? model.model_id}</span>
                          </div>
                        ))}
                        {(detailCatalogEntry?.models ?? []).length === 0 ? <p className="task6-muted">模型目录暂时无法读取。</p> : null}
                      </div>
                    </div>
                  </>
                ) : (
                  <>
                    <div className="task6-provider-head">
                      {nameDraft === null ? (
                        isCustomProviderKind(detail.kind) ? (
                          <>
                            {/* 0912 点点拍板：只有自定义档允许改名（铅笔图标）；内置档名字只读。 */}
                            <button
                              className="task6-provider-name-edit"
                              onClick={() => setNameDraft(detail.name)}
                              title="点击修改显示名"
                              type="button"
                            >
                              {detail.name}
                            </button>
                            <button
                              aria-label="修改显示名"
                              className="task6-provider-mask-btn"
                              onClick={() => setNameDraft(detail.name)}
                              title="修改显示名"
                              type="button"
                            >
                              <IconPencil />
                            </button>
                          </>
                        ) : (
                          <span className="task6-provider-name-plain">{detail.name}</span>
                        )
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
                      <span className="task6-provider-chip">{isCustomProviderKind(detail.kind) ? "自定义 · 协议自动识别" : "内置"}</span>
                      <span className="task6-provider-head-gap" />
                      {/* 设为当前收进卡右上角 ghost 小钮（点点 0912 拍板 b），垃圾桶左侧。 */}
                      {!detail.is_default ? <Button disabled={switchingProvider} onClick={() => void makeActive(detail.id)} size="compact" variant="ghost">设为当前</Button> : null}
                      {/* 删除档案收进标题行垃圾桶图标（线框），确认弹窗与保留门槛不变。 */}
                      <button
                        aria-label="删除此档案"
                        className="task6-provider-mask-btn"
                        data-danger="true"
                        disabled={lastKeeper || detail.is_default}
                        onClick={() => setConfirmAction({
                          title: "删除 Provider",
                          impact: "删除此本地 Provider 配置与 credential，不会删除 Analysis。",
                          run: async () => {
                            await deleteProviderProfile(detail.id);
                            setSelectedId((current) => (current === detail.id ? null : current));
                          },
                        })}
                        title={lastKeeper ? "至少保留一个档案" : detail.is_default ? "当前使用中的档案不能删除，请先切换" : "删除此档案"}
                        type="button"
                      >
                        <IconTrash />
                      </button>
                    </div>

                    <dl className="task6-provider-conn">
                      <div className="task6-provider-conn-row">
                        <dt>Base URL</dt>
                        <dd>
                          {isCustomProviderKind(detail.kind) ? (
                            <FieldControl
                              aria-label="Base URL"
                              autoComplete="off"
                              /* 点点 0912 拍板：行内保存钮退役，失焦/回车即存（未变更时是空操作）。 */
                              onBlur={() => void saveBaseUrl(detail)}
                              onChange={(event) => setBaseUrlDraft(event.target.value)}
                              onKeyDown={(event) => { if (event.key === "Enter") void saveBaseUrl(detail); }}
                              value={baseUrlDraft ?? detail.base_url ?? ""}
                            />
                          ) : (
                            /* 0912 点点拍板：内置端点不可改，但用禁用输入框兜住同样几何，
                                切换自定义档时内容不再位移。 */
                            <FieldControl
                              aria-label="Base URL（内置端点，不可修改）"
                              className="task6-provider-baseurl-readonly"
                              disabled
                              readOnly
                              tabIndex={-1}
                              value={detailCatalogEntry?.base_url || detail.base_url || "由服务商标定"}
                            />
                          )}
                        </dd>
                      </div>
                      <div className="task6-provider-conn-row">
                        <dt>API Key</dt>
                        <dd>{renderKeyMaskRow(detail)}</dd>
                      </div>
                    </dl>

                    {/* 模型列表（0912 线框补齐）：内置=目录只读行+⟳刷新；
                        自定义=存档列表直接显示（点点 0912 拍板：先存一份），
                        「获取模型」只做更新。 */}
                    <div className="task6-provider-models">
                      <div className="task6-provider-models-title">模型列表</div>
                      {isCustomProviderKind(detail.kind) ? (
                        detailModels.phase === "loading" ? <p className="task6-muted" aria-live="polite">正在读取可用模型…</p>
                        : detailModels.phase === "error" ? <p className="task6-provider-model-error">{detailModels.message}</p>
                        : detailModels.phase === "ready" ? (
                          <div className="task6-provider-models-list">
                            {detailModels.models.map((modelId) => (
                              <div className="task6-provider-model-row" key={modelId}><span>{modelId}</span></div>
                            ))}
                          </div>
                        ) : (detail.discovered_models?.length ?? 0) > 0 ? (
                          <div className="task6-provider-models-list">
                            {detail.discovered_models!.map((model) => (
                              <div className="task6-provider-model-row" key={model.model_id}><span>{model.model_id}</span></div>
                            ))}
                          </div>
                        ) : null
                      ) : (
                        <div className="task6-provider-models-list">
                          {(detailCatalogEntry?.models ?? []).map((model) => (
                            <div className="task6-provider-model-row" key={model.model_id}>
                              <span>{model.model_name ?? model.model_id}</span>
                            </div>
                          ))}
                          {(detailCatalogEntry?.models ?? []).length === 0 ? <p className="task6-muted">模型目录暂时无法读取。</p> : null}
                        </div>
                      )}
                      <div className="task6-provider-model-actions">
                        {isCustomProviderKind(detail.kind) ? (
                          <Button disabled={detailModels.phase === "loading"} onClick={() => loadDetailModels(detail)} size="compact" variant="ghost">⟳ 获取模型</Button>
                        ) : (
                          <Button disabled={detailCatalogReloading} onClick={reloadDetailCatalog} size="compact" variant="ghost">⟳ 获取模型</Button>
                        )}
                      </div>
                    </div>

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
                  </>
                )}
              </article>
              </Panel>
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
                    {event.type === "auth_url" ? <a href={event.url} rel="noreferrer" target="_blank" onClick={(e) => handleExternalClick(e, event.url)}>打开 Provider 授权页</a> : null}
                    {event.type === "device_code" ? <p>设备码：<strong>{event.user_code}</strong> · <a href={event.verification_uri} rel="noreferrer" target="_blank" onClick={(e) => handleExternalClick(e, event.verification_uri)}>前往验证</a></p> : null}
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
        title={`添加 Provider · 第 ${wizardStep}/${WIZARD_STEP_COUNT} 步`}
        footer={
          wizardStep === 1 ? (
            <>
              <Button onClick={closeWizard} variant="secondary">取消</Button>
              <Button
                disabled={!wizardStepReady(wizardStep, wizardDraft, Boolean(wizardCatalogEntry))}
                onClick={() => setWizardStep(2)}
                variant="primary"
              >
                下一步
              </Button>
            </>
          ) : (
            <>
              <Button disabled={wizardCheckingNow} onClick={() => setWizardStep(1)} variant="secondary">上一步</Button>
              <Button
                disabled={wizardVerified ? !wizardPayload : !wizardProbePayload}
                onClick={() => {
                  if (wizardVerified) {
                    void finishWizard().catch(() => notify("Provider 未能添加，请检查输入后重试。"));
                    return;
                  }
                  void runWizardCheck();
                }}
                variant="primary"
              >
                {wizardVerified ? "完成" : "测试"}
              </Button>
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

        {wizardStep === 1 ? (
          <div className="task6-wizard-type-grid" role="radiogroup" aria-label="Provider 类型">
            {wizardTypeList.map((type) => {
              const available = type.custom || Boolean(wizardCatalogProvider(wizardCatalogSource, type.id));
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
                  {available ? null : <span className="task6-muted">当前目录中暂不可用</span>}
                </label>
              );
            })}
          </div>
        ) : (
          <div className="task6-wizard-step-body">
            <Field label="显示名称">
              <FieldControl
                onChange={(event) => wizardSetDraft({ name: event.target.value })}
                placeholder={wizardDefaultName(wizardCatalogSource, wizardDraft.typeId)}
                value={wizardDraft.name}
              />
              {wizardNameConflict ? (
                <p className="task6-muted">已有同名档案，建议换一个名字以便区分。</p>
              ) : null}
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
            <p className="task6-muted">此步尚未写入档案。</p>
            {wizardVerified ? (
              wizardCustom ? (
                <>
                  {customModelState === "loading" ? <p className="task6-muted" aria-live="polite">正在读取可用模型…</p> : null}
                  {customModelState === "loaded" ? (
                    <Field label="Model">
                      <select className="ac-field__control" onChange={(event) => wizardSetDraft({ modelId: event.target.value })} value={wizardDraft.modelId}>
                        <option value="">选择 Model</option>
                        {customModels.map((model) => <option key={model.model_id} value={model.model_id}>{model.model_id}</option>)}
                      </select>
                      <span className="task6-inline-actions">
                        <Button onClick={() => { wizardSetDraft({ modelId: "" }); customDiscovery.enterManualMode(); }} size="compact" variant="ghost">列表中没有需要的 Model ID</Button>
                        <Button onClick={customDiscovery.refresh} size="compact" variant="ghost">获取模型</Button>
                      </span>
                    </Field>
                  ) : null}
                  {customModelState === "manual" ? (
                    <Field label="Model ID">
                      <FieldControl autoComplete="off" onChange={(event) => wizardSetDraft({ modelId: event.target.value })} value={wizardDraft.modelId} />
                      <Button onClick={customDiscovery.refresh} size="compact" variant="ghost">获取模型</Button>
                    </Field>
                  ) : null}
                  {customModelMessage ? <p className="task6-muted" aria-live="polite">{customModelMessage}</p> : null}
                </>
              ) : (
                <Field label="Model">
                  <span className="task6-provider-keydraft">
                    <select className="ac-field__control" onChange={(event) => wizardSetDraft({ modelId: event.target.value })} value={wizardDraft.modelId}>
                      <option value="">选择 Model</option>
                      {wizardModelOptions.map((model) => <option key={model.id} value={model.id}>{model.label}</option>)}
                    </select>
                    <Button disabled={wizardCatalogReloading} onClick={reloadWizardCatalog} size="compact" variant="ghost">
                      {wizardCatalogReloading ? "正在获取…" : "获取模型"}
                    </Button>
                  </span>
                </Field>
              )
            ) : (
              <p className="task6-muted">测试通过后在此获取并选择模型。</p>
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
            {wizardVerified && !wizardPayload ? (
              <p className="task6-muted">选择模型后点「完成」保存。</p>
            ) : null}
            {wizardCustom && wizardVerified && !customProtocolConfirmed && wizardDraft.apiKey && wizardDraft.baseUrl ? (
              <p className="task6-muted">无法自动识别接口协议时，将回退为手动填写 Model ID；可调整端点后重新测试。</p>
            ) : null}
            {wizardCheckingNow ? <p className="task6-muted" aria-live="polite">正在测试连接…再次点「测试」可取消。</p> : null}
            {wizardCheck.phase === "done" && wizardCheck.fingerprint === wizardFingerprint ? (
              wizardCheck.passed
                ? <p className="task6-ok" aria-live="polite">✓ 连接成功</p>
                : <Notice tone="error">连接失败</Notice>
            ) : null}
            {wizardCheck.phase === "done" && !wizardCheck.passed && wizardCheck.fingerprint === wizardFingerprint ? (
              <p className="task6-muted">{wizardCheck.message}</p>
            ) : null}
            {!wizardVerified && wizardCheck.phase === "idle" ? (
              <p className="task6-muted">点「测试」验证这份配置；通过后才能完成添加。</p>
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
    </>
  );
}
