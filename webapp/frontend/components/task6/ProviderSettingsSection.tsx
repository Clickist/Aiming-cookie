"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import {
  authorizeProviderProfile,
  cancelProviderAuthOperation,
  createProviderProfile,
  deleteProviderProfile,
  discoverCustomProviderModels,
  getProviderAuthOperation,
  getProviderCatalog,
  getProviderCredential,
  fetchMemberStatus,
  listStoredCustomProviderModels,
  logoutMemberAccount,
  startMemberLogin,
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
import { MEMBER_COPY, formatMemberDate, planLabel, poolTier } from "@/lib/member";
import { MEMBER_STATE_CHANGED_EVENT } from "@/lib/member-state";
import { t, useT, type MessageKey } from "@/lib/i18n";
import { ACCOUNTS_BASE_URL } from "@/lib/infra-urls";
import { parseTrialState } from "@/lib/trial";
import type {
  MemberMe,
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

/** 会员档的账号中心入口（订阅管理与退款都在网页账单子页；客户端无支付界面）。 */
const ACCOUNT_BILLING_URL = `${ACCOUNTS_BASE_URL}/account/billing`;
/** 已验证待订阅的订阅入口（与付费墙同一落点；客户端内无支付界面）。 */
const TRIAL_SUBSCRIBE_URL = `${ACCOUNTS_BASE_URL}/pay`;

/** 会员态模块级缓存（点点 0927：重进设置/切档回来不重载——详情先用上次数据
 * 立即呈现，后台静默刷新后就地更新，无空白期）。组件卸载不清空。 */
let memberMeCache: MemberMe | null = null;

/** 订阅池下方的加油包小行（没买过包时不出现——线框：零负担）。 */
function boosterSubline(me: MemberMe): string {
  const boost = me.pools.boost;
  if (!boost || boost.remaining <= 0) return MEMBER_COPY.quotaPerCycle;
  return `${MEMBER_COPY.boosterRow} · ${MEMBER_COPY.boosterRemain(boost.pct)}`;
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

const WIZARD_STEP_LABELS = ["settings.provider.wizardStepType", "settings.provider.wizardStepCredential"] as const satisfies readonly MessageKey[];

// 向导力度下拉的全部可选档（与 composer CoachModelMenu 的 EFFORT_OPTIONS 同
// 键同序）；1003 起按模型真支持档（目录 reasoning_efforts）过滤渲染，写死
// 全量会在不支持的模型上说谎——pi clamp 静默收敛令选中档≠实际运行档。
const WIZARD_EFFORT_OPTIONS: ReadonlyArray<{ value: ProviderReasoningEffort; label: MessageKey }> = [
  { value: "off", label: "coach.effort.off" },
  { value: "minimal", label: "coach.effort.minimal" },
  { value: "low", label: "coach.effort.low" },
  { value: "medium", label: "coach.effort.medium" },
  { value: "high", label: "coach.effort.high" },
];

/** 会员档未建档时的合成档案（未登录也可选中查看会员模板）。
 *  i18n 批 4：合成档案在调用时构造，name 经 t() 取「Aiming Cookie（推荐）」
 *  （复用批 1 键 member.provider.relayLabel）——合成档案只用于本进程渲染，
 *  不会入库，调用时取词即随语言。 */
function syntheticOfficialProfile(): ProviderProfile {
  return {
    id: -1,
    name: t("member.provider.relayLabel"),
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
}

function providerStateLabel(status: ProviderProfileState): string {
  switch (status) {
    case "unconfigured": return t("settings.provider.stateUnconfigured");
    case "auth_expired": return t("settings.provider.stateAuthExpired");
    case "needs_reauth": return t("settings.provider.stateNeedsReauth");
    case "ready": return t("settings.provider.stateReady");
    case "model_unavailable": return t("settings.provider.stateModelUnavailable");
    case "connection_failed": return t("settings.provider.stateConnectionFailed");
  }
}

function authOperationLabel(status: ProviderAuthOperation["status"]): string {
  switch (status) {
    case "running": return t("settings.provider.authRunning");
    case "awaiting_input": return t("settings.provider.authAwaitingInput");
    case "succeeded": return t("settings.provider.authSucceeded");
    case "failed": return t("settings.provider.authFailed");
    case "cancelled": return t("settings.provider.authCancelled");
    case "timed_out": return t("settings.provider.authTimedOut");
  }
}

function providerTypeLabel(profile: ProviderProfile, catalog: ProviderCatalogV1 | null): string {
  if (isCustomProviderKind(profile.kind)) {
    return profile.kind === "custom_anthropic_compatible" ? t("settings.provider.kindAnthropic") : t("settings.provider.kindOpenai");
  }
  const entry = catalog?.providers.find((provider) => provider.provider_id === profile.provider_id);
  return entry?.provider_name ?? profile.provider_id ?? t("settings.provider.builtinFallback");
}

/** 测活时间的人话相对时长（线框：上次测活成功 · N 小时前）。 */
function relativeTime(value: string | null | undefined): string {
  if (!value) return "";
  const time = new Date(value).getTime();
  if (Number.isNaN(time)) return "";
  const minutes = Math.max(0, Math.round((Date.now() - time) / 60_000));
  if (minutes < 1) return t("settings.provider.relativeJustNow");
  if (minutes < 60) return t("settings.provider.relativeMinutes", { n: minutes });
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return t("settings.provider.relativeHours", { n: hours });
  return t("settings.provider.relativeDays", { n: Math.floor(hours / 24) });
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
  const t = useT();
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
  // 会员档（aiming-cookie-relay）账号视图（WP-C）：登录 + 会员态。
  // 旧的「会员计划 / API 计费」二选与余额查询已退役——额度只以百分比由
  // `/api/me` 下发（契约 §7.1-8），key 对用户不可见。
  const [memberMe, setMemberMe] = useState<MemberMe | null>(memberMeCache);
  const [memberBusy, setMemberBusy] = useState(false);
  // 已存自定义档详情的模型发现（点「获取模型」后才有内容；内置档走目录刷新）。
  const [detailModels, setDetailModels] = useState<
    { phase: "idle" | "loading" | "ready" | "error"; models: string[]; message: string | null }
  >({ phase: "idle", models: [], message: null });
  // 内置档详情「获取模型」：重新拉取 sidecar 目录快照。
  const [detailCatalogReload, setDetailCatalogReload] = useState<ProviderCatalogV1 | null>(null);
  const [detailCatalogReloading, setDetailCatalogReloading] = useState(false);

  const loadMemberMe = () => {
    void fetchMemberStatus()
      .then((status) => {
        const next = status.ok && status.logged_in ? status.me : null;
        memberMeCache = next;
        setMemberMe(next);
      })
      .catch(() => {
        // 静默失败：保留现有（缓存的）展示，不打断详情区。
      });
  };

  // 打开会员档详情即静默刷新一次会员态（0927 点点：每次都刷新，数据就地
  // 更新；展示层用模块级缓存兜底，刷新期间不清空内容）。
  useEffect(() => {
    if (!officialSelected) return;
    loadMemberMe();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [officialSelected]);

  // 登录 deep-link 成功后 AppShell 跳 /settings?provider=official#llm-provider：
  // 挂载时认领 query 参数，直接打开官方档详情（验证闸试用块的收口页）。
  // 选屏由 SettingsWorkspace 既有的 hash 深链负责。
  useEffect(() => {
    if (new URLSearchParams(window.location.search).get("provider") === "official") {
      setOfficialSelected(true);
    }
  }, []);

  // 会员态广播（登录换票成功 / 退出 / 试用上报后的刷新）：官方档详情的会员
  // 视图与试用块跟上服务端账本，不必重开设置页。
  useEffect(() => {
    const onChanged = () => loadMemberMe();
    window.addEventListener(MEMBER_STATE_CHANGED_EVENT, onChanged);
    return () => window.removeEventListener(MEMBER_STATE_CHANGED_EVENT, onChanged);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /** 设置页内的会员登录（①a 同链路）：起 device_code → 系统浏览器 → deep-link 回来刷新。 */
  const startMemberLoginFromSettings = () => {
    if (memberBusy) return;
    setMemberBusy(true);
    void startMemberLogin()
      .then(async (result) => {
        if (!result.ok) {
          notify(result.message);
          return;
        }
        await openExternalUrl(result.login_url);
        notify(t("settings.provider.browserOpened"));
      })
      .catch(() => notify(t("settings.provider.loginFailed")))
      .finally(() => setMemberBusy(false));
  };

  /** 外链统一出口（桌面端走 opener，浏览器预览新标签）。 */
  const openExternalUrl = (url: string) => {
    void import("@/lib/desktop").then(({ openExternalUrl: open }) => open(url));
  };

  const logoutMemberFromSettings = () => {
    if (memberBusy) return;
    setMemberBusy(true);
    void logoutMemberAccount()
      .then(async () => {
        notify(t("settings.provider.loggedOut"));
        await refresh(true);
        loadMemberMe();
      })
      .catch(() => notify(t("settings.provider.logoutFailed")))
      .finally(() => setMemberBusy(false));
  };

  /** 已存自定义档「获取模型」：只传 profile_id，key 留在 sidecar 就地发现并
   *  更新存档列表（点点 0912 拍板）；成功后刷新投影，下次直接显示。 */
  const loadDetailModels = (profile: ProviderProfile) => {
    setDetailModels({ phase: "loading", models: [], message: null });
    void listStoredCustomProviderModels(profile.id)
      .then(async (next) => {
        setDetailModels(next.models.length
          ? { phase: "ready", models: next.models.map((model) => model.model_id), message: null }
          : { phase: "error", models: [], message: t("settings.provider.noModels") });
        await refresh(true);
      })
      .catch(() => setDetailModels({ phase: "error", models: [], message: t("settings.provider.discoveryFailed") }));
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
  // 1003：同一次 find 顺带取模型真支持档位表，下拉按它渲染（与 composer
  // 力度菜单同源同行为）；旧 sidecar 无此字段回落全五档。
  const wizardSelectedCatalogModel = wizardCatalogEntry?.models.find((model) => model.model_id === wizardDraft.modelId);
  const wizardModelIsReasoning = !wizardCustom && wizardSelectedCatalogModel?.reasoning === true;
  const wizardModelEfforts = wizardSelectedCatalogModel?.reasoning_efforts;
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
          ? t("settings.provider.wizardCheckPassedDetail", { name: payload.name })
          : t("settings.provider.wizardCheckFailedDetail", { message: status.message }),
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
        message: t("settings.provider.wizardConfirmRetry", {
          message: error instanceof Error && error.message ? error.message : t("settings.provider.wizardCannotConnect"),
        }),
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
      .catch(() => notify(t("settings.provider.catalogRefreshFailed")))
      .finally(() => setWizardCatalogReloading(false));
  };

  const makeActive = async (profileId: number) => {
    if (switchingProvider) return;
    setSwitchingProvider(true);
    try {
      await setDefaultProviderProfile(profileId);
      await refresh(true);
    } catch {
      notify(t("settings.provider.defaultFailed"));
    } finally {
      setSwitchingProvider(false);
    }
  };

  const testConnection = async (profile: ProviderProfile) => {
    setTestingConnection(true);
    try {
      const status = await testProviderProfile(profile.id);
      setLastTest({ passed: status.status === "ready", message: t("settings.provider.testResult", { name: profile.name, message: status.message }) });
      await refresh(true);
    } catch {
      // 测连失败只标错误，不删档案。
      setLastTest({ passed: false, message: t("settings.provider.testFailed", { name: profile.name }) });
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
      notify(t("settings.provider.nameSaveFailed"));
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
      notify(t("settings.provider.baseUrlSaveFailed"));
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
          // i18n 批 4（文案入库边界，同 onboarding customProviderName 拍板）：
          // 档案名在创建时按当前 locale 定型入库，切语言不改已存档案名。
          name: t("settings.provider.officialArchiveName"),
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
      notify(t("settings.provider.keySaved"));
      await refresh(true);
    } catch {
      notify(t("settings.provider.keySaveFailed"));
    }
  };

  const startAuthorization = async (profileId: number) => {
    const operation = await authorizeProviderProfile(profileId, "oauth");
    setAuthProfileId(profileId);
    setAuthOperation(operation);
    setAuthPromptValue("");
    notify(t("settings.provider.authorizePrompt"));
  };

  const submitAuthPrompt = async () => {
    const prompt = authOperation?.prompts[0];
    if (!authOperation || !prompt || !authPromptValue.trim()) return;
    try {
      setAuthOperation(await submitProviderAuthInput(authOperation.id, prompt.prompt_id, authPromptValue));
      setAuthPromptValue("");
    } catch {
      notify(t("settings.provider.authInputRejected"));
    }
  };

  const cancelAuthorization = async () => {
    if (!authOperation) return;
    try {
      setAuthOperation(await cancelProviderAuthOperation(authOperation.id));
      notify(t("settings.provider.authCancelledNotice"));
    } catch {
      notify(t("settings.provider.authCancelFailed"));
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
            notify(t("settings.provider.authSucceededTest"));
            await refresh(true);
          }
        })
        .catch(() => notify(t("settings.provider.authUnreadable")));
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
  const detail = officialSelected ? (relayArchive ?? syntheticOfficialProfile()) : selectedProfile;
  // AC 验证闸试用态（与 MemberCenter 同源：/me 的 trial 字段宽松解析）。
  const trial = memberMe ? parseTrialState(memberMe) : null;
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
      .catch(() => notify(t("settings.provider.catalogRefreshFailed")))
      .finally(() => setDetailCatalogReloading(false));
  };

  // Key 掩码行（0912 拍板两段语义）：眼睛=显示/隐藏存档明文 Key；点 Key 文字
  // =进入粘贴编辑（回车/失焦即存、Esc 取消）。非 api_key 档回退只读掩码文本。
  const renderKeyMaskRow = (profile: ProviderProfile) => {
    if (!selectedAuthModes.includes("api_key")) {
      return <span className="task6-mono">{profile.credential_configured ? "••••••••" : t("settings.provider.stateUnconfigured")}</span>;
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
            title={t("settings.provider.keyChangeTitle")}
            type="button"
          >
            {revealed ?? (profile.credential_configured ? "••••••••" : t("settings.provider.stateUnconfigured"))}
          </button>
          <button
            aria-label={revealed ? t("settings.provider.hideKey") : t("settings.provider.showKey")}
            className="task6-provider-mask-btn"
            onClick={() => {
              if (revealed) { setRevealedKey(null); return; }
              if (!profile.credential_configured) { setKeyEditing(true); return; }
              void getProviderCredential(profile.id)
                .then((next) => setRevealedKey({ id: profile.id, key: next.api_key }))
                .catch(() => notify(t("settings.provider.keyUnreadable")));
            }}
            title={revealed ? t("settings.provider.hideKey") : t("settings.provider.showKey")}
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
          aria-label={t("settings.provider.pasteKeyAria")}
          autoFocus
          autoComplete="off"
          className="task6-provider-keymask-input"
          onBlur={() => { if (credentialDraft) void saveNewCredential(profile); else setKeyEditing(false); }}
          onChange={(event) => setCredentialDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") void saveNewCredential(profile);
            if (event.key === "Escape") { setCredentialDraft(""); setKeyEditing(false); }
          }}
          placeholder={t("settings.provider.pasteKeyPlaceholder")}
          type="text"
          value={credentialDraft}
        />
      </span>
    );
  };

  return (
    <>
      {loading ? (
        <Loading>{t("settings.provider.loadingSettings")}</Loading>
      ) : (
        <div className="task6-provider-master">
          {/* 0912 线框拍板：列表与详情是两张自包含卡，不再共裹一块大板。 */}
          <Panel className="task6-provider-list-card">
            <div aria-label={t("settings.provider.listAria")} className="task6-provider-list">
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
              }}
              role="listitem"
              type="button"
            >
              <span className="task6-provider-list-text">
                <span className="task6-provider-list-name">{t("member.provider.relayLabel")}</span>
                <span className="task6-provider-list-type">{t("settings.provider.builtin")}</span>
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
                }}
                role="listitem"
                type="button"
              >
                <span className="task6-provider-list-text">
                  <span className="task6-provider-list-name">{profile.name}</span>
                  <span className="task6-provider-list-type">{isCustomProviderKind(profile.kind) ? t("settings.provider.custom") : t("settings.provider.builtin")}</span>
                </span>
                {/* 行尾绿点＝连接正常，红点＝探测不通（线框；数据来自自动测活）。 */}
                <span aria-hidden="true" className="task6-provider-dot" data-ready={profile.status === "ready"} />
              </button>
            ))}
            {profiles.length === 0 ? <p className="task6-muted">{t("settings.provider.emptyProfiles")}</p> : null}
            <Button className="task6-provider-add" onClick={() => void openWizard()} variant="primary">
              {t("settings.provider.addService")}
            </Button>
            </div>
          </Panel>

          <div className="task6-provider-detail-pane">
            {!detail ? (
              <div className="task6-provider-empty">
                <p className="task6-muted">{t("settings.provider.emptyDetail")}</p>
              </div>
            ) : (
              <Panel className="task6-provider-detail-card">
              <article className="task6-provider-detail" key={detail.id}>
                {officialDetail ? (
                  <>
                    <div className="task6-provider-head">
                      <span className="task6-provider-name-plain">{t("member.provider.relayLabel")}</span>
                      <span className="task6-provider-head-gap" />
                      {/* 设为当前收进卡右上角 ghost 小钮（点点 0912 拍板 b）。 */}
                      {relayArchive && !relayArchive.is_default ? (
                        <Button disabled={switchingProvider} onClick={() => void makeActive(relayArchive.id)} size="compact" variant="ghost">{t("settings.provider.makeActive")}</Button>
                      ) : null}
                    </div>

                    {/* 会员专属模板（WP-C）：套餐 / 余量 / 登录与退出。不显示
                        Base URL、API key，也没有任何余额金额——额度只出百分比。 */}
                    {memberMe ? (
                      <>
                        <div className="task6-provider-plan" data-member="true">
                          <div className="task6-provider-plan-head">
                            <strong>{memberMe.member ? t("settings.provider.memberPlan", { plan: planLabel(memberMe.plan) }) : t("settings.provider.loggedInUnsubscribed")}</strong>
                            <span className="task6-provider-head-gap" />
                            <span className="task6-muted">{memberMe.user.email}</span>
                          </div>
                          <p className="task6-provider-plan-meta">
                            {memberMe.member
                              ? (memberMe.cancel_at_period_end
                                  ? t("settings.provider.canceledUntil", { date: formatMemberDate(memberMe.period_end) })
                                  : t("settings.provider.autoRenewUntil", { date: formatMemberDate(memberMe.period_end) }))
                              : t("settings.provider.noSubscription")}
                          </p>
                        </div>
                        {/* AC 验证闸试用块（点点 0926：官方档详情也要把免费额度
                            说清）：未订阅且 /me 带 trial 态才渲染——试用中=剩余
                            次数+说明+去跑一局；已验证=可订阅+订阅入口。已订阅
                            （会员视图不变）与无试用态都不渲染。 */}
                        {!memberMe.member && trial ? (
                          trial.verified ? (
                            <div className="task6-provider-plan" data-member="true" data-trial-verified="true">
                              <div className="task6-provider-plan-head">
                                <strong>{t("trial.center.verified")}</strong>
                              </div>
                              <p className="task6-provider-plan-meta">{t("trial.center.verifiedHint")}</p>
                              <div className="task6-provider-member-actions">
                                <Button onClick={() => void openExternalUrl(TRIAL_SUBSCRIBE_URL)} size="compact" variant="primary">
                                  {t("trial.paywall.subscribe")}
                                </Button>
                              </div>
                            </div>
                          ) : (
                            <div className="task6-provider-plan" data-member="true">
                              <div className="task6-provider-plan-head">
                                <strong>{t("trial.center.title")}</strong>
                              </div>
                              <p className="task6-provider-plan-meta">
                                {t("trial.center.remaining", { analyses: trial.analysesRemaining, questions: trial.questionsRemaining })}
                              </p>
                              <p className="task6-provider-plan-meta">{t("trial.center.hint")}</p>
                              <p className="task6-provider-plan-meta">{t("trial.settings.guide")}</p>
                            </div>
                          )
                        ) : null}
                        <div className="task6-provider-quota-label">{t("settings.provider.quotaLabel")}</div>
                        <div className="task6-provider-quota" data-member="true">
                          <div className="task6-provider-quota-head">
                            {memberMe.pools.sub ? (memberMe.member ? t("settings.provider.subPoolCurrent") : t("settings.provider.subPoolPeriod")) : t("settings.provider.subPool")}
                          </div>
                          <div className="task6-provider-quota-value">
                            <b>{memberMe.pools.sub ? `${memberMe.pools.sub.pct}%` : "—"}</b>
                            <span className="task6-provider-quota-sub">{boosterSubline(memberMe)}</span>
                          </div>
                          <div className="task6-provider-quota-meter">
                            <i
                              data-tier={memberMe.pools.sub ? poolTier(memberMe.pools.sub.pct) : undefined}
                              style={{ width: `${Math.max(0, Math.min(100, memberMe.pools.sub?.pct ?? 0))}%` }}
                            />
                          </div>
                        </div>
                        <div className="task6-provider-member-actions">
                          <Button onClick={() => void openExternalUrl(ACCOUNT_BILLING_URL)} size="compact" variant="secondary">
                            {t("settings.provider.manageSubscription")}
                          </Button>
                          <Button disabled={memberBusy} onClick={logoutMemberFromSettings} size="compact" variant="ghost">
                            {t("member.logout.button")}
                          </Button>
                        </div>
                      </>
                    ) : (
                      <>
                        <div className="task6-provider-plan" data-member="true">
                          <div className="task6-provider-plan-head">
                            <strong>{t("settings.provider.notLoggedIn")}</strong>
                          </div>
                          <p className="task6-provider-plan-meta">
                            {t("settings.provider.notLoggedInBody")}
                          </p>
                        </div>
                        <div className="task6-provider-member-actions">
                          <Button disabled={memberBusy} onClick={startMemberLoginFromSettings} size="compact" variant="primary">
                            {memberBusy ? t("settings.provider.openingBrowser") : t("settings.provider.loginButton")}
                          </Button>
                        </div>
                      </>
                    )}

                    <div className="task6-provider-models">
                      <div className="task6-provider-models-title">{t("settings.provider.modelsTitle")}</div>
                      <div className="task6-provider-models-list">
                        <div className="task6-provider-model-row" key="deepseek-v4-flash">
                          <span>deepseek-v4-flash</span>
                          <span className="task6-muted">{t("settings.provider.officialModelNote")}</span>
                        </div>
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
                              title={t("settings.provider.editNameTitle")}
                              type="button"
                            >
                              {detail.name}
                            </button>
                            <button
                              aria-label={t("settings.provider.editNameAria")}
                              className="task6-provider-mask-btn"
                              onClick={() => setNameDraft(detail.name)}
                              title={t("settings.provider.editNameAria")}
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
                          aria-label={t("settings.provider.nameInputAria")}
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
                      <span className="task6-provider-chip">{isCustomProviderKind(detail.kind) ? t("settings.provider.customProtocolChip") : t("settings.provider.builtin")}</span>
                      <span className="task6-provider-head-gap" />
                      {/* 设为当前收进卡右上角 ghost 小钮（点点 0912 拍板 b），垃圾桶左侧。 */}
                      {!detail.is_default ? <Button disabled={switchingProvider} onClick={() => void makeActive(detail.id)} size="compact" variant="ghost">{t("settings.provider.makeActive")}</Button> : null}
                      {/* 删除档案收进标题行垃圾桶图标（线框），确认弹窗与保留门槛不变。 */}
                      <button
                        aria-label={t("settings.provider.deleteAria")}
                        className="task6-provider-mask-btn"
                        data-danger="true"
                        disabled={lastKeeper || detail.is_default}
                        onClick={() => setConfirmAction({
                          title: t("settings.provider.deleteTitle"),
                          impact: t("settings.provider.deleteImpact"),
                          run: async () => {
                            await deleteProviderProfile(detail.id);
                            setSelectedId((current) => (current === detail.id ? null : current));
                          },
                        })}
                        title={lastKeeper ? t("settings.provider.deleteLastKeeper") : detail.is_default ? t("settings.provider.deleteDefault") : t("settings.provider.deleteAria")}
                        type="button"
                      >
                        <IconTrash />
                      </button>
                    </div>

                    <dl className="task6-provider-conn">
                      <div className="task6-provider-conn-row">
                        <dt>{t("settings.provider.baseUrlLabel")}</dt>
                        <dd>
                          {isCustomProviderKind(detail.kind) ? (
                            <FieldControl
                              aria-label={t("settings.provider.baseUrlAria")}
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
                              aria-label={t("settings.provider.baseUrlBuiltinAria")}
                              className="task6-provider-baseurl-readonly"
                              disabled
                              readOnly
                              tabIndex={-1}
                              value={detailCatalogEntry?.base_url || detail.base_url || t("settings.provider.baseUrlByProvider")}
                            />
                          )}
                        </dd>
                      </div>
                      <div className="task6-provider-conn-row">
                        <dt>{t("settings.provider.apiKeyLabel")}</dt>
                        <dd>{renderKeyMaskRow(detail)}</dd>
                      </div>
                    </dl>

                    {/* 模型列表（0912 线框补齐）：内置=目录只读行+⟳刷新；
                        自定义=存档列表直接显示（点点 0912 拍板：先存一份），
                        「获取模型」只做更新。 */}
                    <div className="task6-provider-models">
                      <div className="task6-provider-models-title">{t("settings.provider.modelsListTitle")}</div>
                      {isCustomProviderKind(detail.kind) ? (
                        detailModels.phase === "loading" ? <p className="task6-muted" aria-live="polite">{t("settings.provider.loadingModels")}</p>
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
                          {(detailCatalogEntry?.models ?? []).length === 0 ? <p className="task6-muted">{t("settings.provider.catalogUnreadable")}</p> : null}
                        </div>
                      )}
                      <div className="task6-provider-model-actions">
                        {isCustomProviderKind(detail.kind) ? (
                          <Button disabled={detailModels.phase === "loading"} onClick={() => loadDetailModels(detail)} size="compact" variant="ghost">{t("settings.provider.getModels")}</Button>
                        ) : (
                          <Button disabled={detailCatalogReloading} onClick={reloadDetailCatalog} size="compact" variant="ghost">{t("settings.provider.getModels")}</Button>
                        )}
                      </div>
                    </div>

                    {selectedAuthModes.includes("oauth") ? (
                      <div className="task6-inline-actions">
                        <Button
                          onClick={() => setConfirmAction({
                            title: t("settings.provider.reauthTitle"),
                            impact: t("settings.provider.reauthImpact"),
                            run: async () => { await startAuthorization(detail.id); },
                          })}
                          size="compact"
                          variant="secondary"
                        >
                          {t("settings.provider.reauthButton")}
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
                  <span className="task6-auth-operation-title">{t("settings.provider.authSectionTitle")}</span>
                  <Status tone={authOperation.status === "succeeded" ? "success" : authOperation.status === "failed" || authOperation.status === "timed_out" ? "error" : "info"}>
                    {authOperationLabel(authOperation.status)}
                  </Status>
                </div>
                {authOperation.events.map((event, index) => (
                  <div key={`${event.type}-${index}`}>
                    {event.type === "auth_url" ? <a href={event.url} rel="noreferrer" target="_blank" onClick={(e) => handleExternalClick(e, event.url)}>{t("settings.provider.openAuthPage")}</a> : null}
                    {event.type === "device_code" ? <p>{t("settings.provider.deviceCode")}<strong>{event.user_code}</strong> · <a href={event.verification_uri} rel="noreferrer" target="_blank" onClick={(e) => handleExternalClick(e, event.verification_uri)}>{t("settings.provider.verifyLink")}</a></p> : null}
                    {event.type === "progress" ? <p>{event.message}</p> : null}
                  </div>
                ))}
                {authOperation.prompts[0] ? (
                  <Field label={authOperation.prompts[0].message}>
                    <div className="task6-inline-actions">
                      {authOperation.prompts[0].type === "select" ? (
                        <select onChange={(event) => setAuthPromptValue(event.target.value)} value={authPromptValue}>
                          <option value="">{t("settings.provider.selectPrompt")}</option>
                          {authOperation.prompts[0].options?.map((option) => <option key={option.id} value={option.id}>{option.label}</option>)}
                        </select>
                      ) : <FieldControl autoComplete="off" onChange={(event) => setAuthPromptValue(event.target.value)} type={authOperation.prompts[0].type === "secret" ? "password" : "text"} value={authPromptValue} />}
                      <Button disabled={!authPromptValue.trim()} onClick={() => void submitAuthPrompt()} variant="secondary">{t("settings.provider.submit")}</Button>
                    </div>
                  </Field>
                ) : null}
                {!isAuthTerminal(authOperation) ? <Button onClick={() => void cancelAuthorization()} variant="ghost">{t("settings.provider.cancelAuth")}</Button> : null}
                {authOperation.error ? <Notice tone="error">{authOperation.error.message}</Notice> : null}
              </section>
            ) : null}
          </div>
        </div>
      )}

      <Dialog
        onClose={closeWizard}
        open={wizardOpen}
        title={t("settings.provider.wizardTitle", { step: wizardStep, total: WIZARD_STEP_COUNT })}
        footer={
          wizardStep === 1 ? (
            <>
              <Button onClick={closeWizard} variant="secondary">{t("settings.dialog.cancel")}</Button>
              <Button
                disabled={!wizardStepReady(wizardStep, wizardDraft, Boolean(wizardCatalogEntry))}
                onClick={() => setWizardStep(2)}
                variant="primary"
              >
                {t("settings.provider.wizardNext")}
              </Button>
            </>
          ) : (
            <>
              <Button disabled={wizardCheckingNow} onClick={() => setWizardStep(1)} variant="secondary">{t("settings.provider.wizardPrev")}</Button>
              <Button
                disabled={wizardVerified ? !wizardPayload : !wizardProbePayload}
                onClick={() => {
                  if (wizardVerified) {
                    void finishWizard().catch(() => notify(t("settings.provider.wizardAddFailed")));
                    return;
                  }
                  void runWizardCheck();
                }}
                variant="primary"
              >
                {wizardVerified ? t("settings.provider.wizardFinish") : t("settings.provider.wizardTest")}
              </Button>
            </>
          )
        }
      >
        <ol className="task6-wizard-steps" aria-label={t("settings.provider.wizardStepsAria")}>
          {WIZARD_STEP_LABELS.map((label, index) => (
            <li
              aria-current={wizardStep === index + 1 ? "step" : undefined}
              data-state={wizardStep > index + 1 ? "done" : wizardStep === index + 1 ? "current" : "todo"}
              key={label}
            >
              {index + 1}. {t(label)}
            </li>
          ))}
        </ol>

        {wizardStep === 1 ? (
          <div className="task6-wizard-type-grid" role="radiogroup" aria-label={t("settings.provider.wizardTypeAria")}>
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
                  {available ? null : <span className="task6-muted">{t("settings.provider.wizardTypeUnavailable")}</span>}
                </label>
              );
            })}
          </div>
        ) : (
          <div className="task6-wizard-step-body">
            <Field label={t("settings.provider.wizardNameField")}>
              <FieldControl
                onChange={(event) => wizardSetDraft({ name: event.target.value })}
                placeholder={wizardDefaultName(wizardCatalogSource, wizardDraft.typeId)}
                value={wizardDraft.name}
              />
              {wizardNameConflict ? (
                <p className="task6-muted">{t("settings.provider.wizardNameConflict")}</p>
              ) : null}
            </Field>
            {wizardCustom ? (
              <Field label={t("settings.provider.baseUrlLabel")} hint={t("settings.provider.wizardBaseUrlHint")}>
                <FieldControl
                  autoComplete="off"
                  onChange={(event) => wizardSetDraft({ baseUrl: event.target.value })}
                  placeholder={customKind === "custom_anthropic_compatible" ? "https://provider.example" : "https://provider.example/v1"}
                  value={wizardDraft.baseUrl}
                />
              </Field>
            ) : (
              <p className="task6-muted">{t("settings.provider.wizardEndpointByProvider")}<span className="task6-mono">{wizardBasePreview || "—"}</span></p>
            )}
            {wizardCustom && wizardBasePreview ? (
              <p className="task6-muted">{t("settings.provider.wizardRequestUrl")}<span className="task6-mono">{wizardBasePreview}</span></p>
            ) : null}
            <Field label={t("settings.provider.wizardApiKeyField")}>
              <span className="task6-provider-keydraft">
                <FieldControl
                  autoComplete="off"
                  onChange={(event) => wizardSetDraft({ apiKey: event.target.value })}
                  type={wizardShowKey ? "text" : "password"}
                  value={wizardDraft.apiKey}
                />
                <Button onClick={() => setWizardShowKey((open) => !open)} size="compact" variant="ghost">{wizardShowKey ? t("settings.provider.wizardHideKey") : t("settings.provider.wizardShowKey")}</Button>
              </span>
            </Field>
            <p className="task6-muted">{t("settings.provider.wizardNotWritten")}</p>
            {wizardVerified ? (
              wizardCustom ? (
                <>
                  {customModelState === "loading" ? <p className="task6-muted" aria-live="polite">{t("settings.provider.loadingModels")}</p> : null}
                  {customModelState === "loaded" ? (
                    <Field label={t("settings.provider.wizardModelField")}>
                      <select className="ac-field__control" onChange={(event) => wizardSetDraft({ modelId: event.target.value })} value={wizardDraft.modelId}>
                        <option value="">{t("settings.provider.wizardSelectModel")}</option>
                        {customModels.map((model) => <option key={model.model_id} value={model.model_id}>{model.model_id}</option>)}
                      </select>
                      <span className="task6-inline-actions">
                        <Button onClick={() => { wizardSetDraft({ modelId: "" }); customDiscovery.enterManualMode(); }} size="compact" variant="ghost">{t("settings.provider.wizardModelIdManual")}</Button>
                        <Button onClick={customDiscovery.refresh} size="compact" variant="ghost">{t("settings.provider.getModels")}</Button>
                      </span>
                    </Field>
                  ) : null}
                  {customModelState === "manual" ? (
                    <Field label={t("settings.provider.wizardModelIdField")}>
                      <FieldControl autoComplete="off" onChange={(event) => wizardSetDraft({ modelId: event.target.value })} value={wizardDraft.modelId} />
                      <Button onClick={customDiscovery.refresh} size="compact" variant="ghost">{t("settings.provider.getModels")}</Button>
                    </Field>
                  ) : null}
                  {customModelMessage ? <p className="task6-muted" aria-live="polite">{customModelMessage}</p> : null}
                </>
              ) : (
                <Field label={t("settings.provider.wizardModelField")}>
                  <span className="task6-provider-keydraft">
                    <select className="ac-field__control" onChange={(event) => wizardSetDraft({ modelId: event.target.value })} value={wizardDraft.modelId}>
                      <option value="">{t("settings.provider.wizardSelectModel")}</option>
                      {wizardModelOptions.map((model) => <option key={model.id} value={model.id}>{model.label}</option>)}
                    </select>
                    <Button disabled={wizardCatalogReloading} onClick={reloadWizardCatalog} size="compact" variant="ghost">
                      {wizardCatalogReloading ? t("settings.provider.wizardFetchingModels") : t("settings.provider.getModels")}
                    </Button>
                  </span>
                </Field>
              )
            ) : (
              <p className="task6-muted">{t("settings.provider.wizardModelsAfterTest")}</p>
            )}
            {wizardModelIsReasoning ? (() => {
              // 档位按模型真支持渲染；旧 sidecar 无 reasoning_efforts 回落全量。
              const effortChoices = wizardModelEfforts
                ? WIZARD_EFFORT_OPTIONS.filter((option) => wizardModelEfforts.includes(option.value))
                : WIZARD_EFFORT_OPTIONS;
              // 编辑存量档案时已存档位可能不在支持表内（pi 运行时仍 clamp 兜底）：
              // 保留显示防止 select 无 matching option 悄悄显示成「默认」的假
              // 一致；用户改选其他档即自然消失。
              const storedExtra = wizardDraft.reasoningEffort && !effortChoices.some((option) => option.value === wizardDraft.reasoningEffort)
                ? WIZARD_EFFORT_OPTIONS.find((option) => option.value === wizardDraft.reasoningEffort) ?? { value: wizardDraft.reasoningEffort, label: "coach.effort.default" as MessageKey }
                : null;
              return (
                <Field label={t("settings.provider.wizardEffortField")}>
                  <select
                    className="ac-field__control"
                    onChange={(event) => wizardSetDraft({ reasoningEffort: event.target.value as ProviderReasoningEffort | "" })}
                    value={wizardDraft.reasoningEffort}
                  >
                    <option value="">{t("settings.provider.wizardEffortDefault")}</option>
                    {effortChoices.map((option) => (
                      <option key={option.value} value={option.value}>{t(option.label)}</option>
                    ))}
                    {storedExtra ? <option value={storedExtra.value}>{t(storedExtra.label)}</option> : null}
                  </select>
                </Field>
              );
            })() : null}
            {wizardVerified && !wizardPayload ? (
              <p className="task6-muted">{t("settings.provider.wizardPickModelToFinish")}</p>
            ) : null}
            {wizardCustom && wizardVerified && !customProtocolConfirmed && wizardDraft.apiKey && wizardDraft.baseUrl ? (
              <p className="task6-muted">{t("settings.provider.wizardProtocolFallback")}</p>
            ) : null}
            {wizardCheckingNow ? <p className="task6-muted" aria-live="polite">{t("settings.provider.wizardCheckingHint")}</p> : null}
            {wizardCheck.phase === "done" && wizardCheck.fingerprint === wizardFingerprint ? (
              wizardCheck.passed
                ? <p className="task6-ok" aria-live="polite">{t("settings.provider.wizardCheckPassed")}</p>
                : <Notice tone="error">{t("settings.provider.wizardCheckFailed")}</Notice>
            ) : null}
            {wizardCheck.phase === "done" && !wizardCheck.passed && wizardCheck.fingerprint === wizardFingerprint ? (
              <p className="task6-muted">{wizardCheck.message}</p>
            ) : null}
            {!wizardVerified && wizardCheck.phase === "idle" ? (
              <p className="task6-muted">{t("settings.provider.wizardTryFirst")}</p>
            ) : null}
          </div>
        )}
      </Dialog>

      <Dialog
        footer={<><Button onClick={() => setConfirmAction(null)} variant="secondary">{t("settings.dialog.cancel")}</Button><Button onClick={() => confirmAction?.run().then(() => { setConfirmAction(null); void refresh(true); }).catch(() => notify(t("settings.feedback.opIncomplete")))} variant="danger">{t("settings.dialog.confirm")}</Button></>}
        onClose={() => setConfirmAction(null)}
        open={Boolean(confirmAction)}
        title={confirmAction?.title ?? t("settings.dialog.confirmTitle")}
      >
        <p>{confirmAction?.impact}</p>
      </Dialog>
    </>
  );
}
