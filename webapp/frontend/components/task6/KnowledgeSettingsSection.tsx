"use client";

import { useCallback, useEffect, useState, type ReactNode } from "react";

import {
  activateKnowledgePack,
  getKnowledgePacks,
  importKnowledgePack,
  KnowledgePackImportError,
  uninstallKnowledgePack,
  type KnowledgePackImportResponseV1,
  type KnowledgePackItemV1,
  type KnowledgePacksResponseV1,
} from "@/lib/api";
import { isDesktopRuntime, pickDesktopDirectory } from "@/lib/desktop";
import { getLocale, t, useT, type MessageKey } from "@/lib/i18n";
import { Badge, Button, Dialog, FieldControl, Loading, Notice, Panel } from "@/ui/primitives";

type ConfirmAction = {
  title: string;
  impact: string;
  run: () => Promise<void>;
} | null;

type WizardStep = 1 | 2 | 3;
type SourceKind = "folder" | "zip";

// 设置页「知识库」栏（kb-sdk WP-12，按点点 2026-09-20 线框实现）：
// 官方档常驻置顶（等同 Provider 官方档语义）+ 已装包单列列表（点行=激活，
// 整库替换、单一生效）+ 三步导入向导（选路径 → 校验结果 → 激活确认）。
// 线框 kb-row/kb-list 等局部类不新增 CSS——行/点/徽标/步骤条复用
// task6-settings.css 现成类，kb 专属细节（回退条动作、错误明细、摘要框）
// 用 token 内联，双主题自然跟随。

// i18n 批 4（§2c）：label/sub 是字典键，渲染时经 t() 解析。
const WIZARD_STEP_LABELS = ["settings.knowledge.wizardStepPick", "settings.knowledge.wizardStepVerify", "settings.knowledge.wizardStepActivate"] as const satisfies readonly MessageKey[];

const OFFICIAL_ROW_SUB_KEY: MessageKey = "settings.knowledge.officialSub";

const SOURCE_KIND_KEYS: Record<SourceKind, { label: MessageKey; sub: MessageKey }> = {
  folder: { label: "settings.knowledge.sourceFolder", sub: "settings.knowledge.sourceFolderSub" },
  zip: { label: "settings.knowledge.sourceZip", sub: "settings.knowledge.sourceZipSub" },
};

/** 「9月18日」短日期；解析失败不硬造（不渲染该片段）。 */
function formatInstalledDay(iso: string): string | null {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return null;
  // 日期格式跟随当前 locale（批 1 formatHistoryDate 同款改法）。
  return new Intl.DateTimeFormat(getLocale() === "en-US" ? "en-US" : "zh-CN", { month: "long", day: "numeric" }).format(date);
}

/** 校验清单行（线框 check-row 语言）：语义符号 + 文本。 */
function CheckRow({ mark, tone, children }: { mark: string; tone: "ok" | "warn" | "bad"; children: ReactNode }) {
  const colors = { ok: "var(--event-kill)", warn: "var(--event-peak)", bad: "var(--error)" } as const;
  return (
    <div style={{ alignItems: "flex-start", display: "flex", fontSize: "var(--text-caption)", gap: "var(--space-2)", lineHeight: 1.55 }}>
      <span aria-hidden="true" style={{ color: colors[tone], fontWeight: 700, flex: "none" }}>{mark}</span>
      <span style={{ minWidth: 0, overflowWrap: "anywhere" }}>{children}</span>
    </div>
  );
}

export function KnowledgeSettingsSection({ notify }: { notify: (message: string) => void }) {
  const t = useT();
  const [desktop, setDesktop] = useState(false);
  const [listing, setListing] = useState<KnowledgePacksResponseV1 | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [confirmAction, setConfirmAction] = useState<ConfirmAction>(null);
  // 坏包回退提示条的「知道了」：仅本次浏览会话内隐藏；提示本身是从 GET
  // 数据推导的近似（active=official 存在坏包，或 active 指针仍指坏包），
  // 如实陈述现状。
  const [fallbackDismissed, setFallbackDismissed] = useState(false);

  const reload = useCallback(async () => {
    try {
      setListing(await getKnowledgePacks());
      setLoadError(false);
    } catch {
      setLoadError(true);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const available = isDesktopRuntime();
    setDesktop(available);
    if (available) void reload();
    else setLoading(false);
  }, [reload]);

  const activate = async (active: string, successMessage: string) => {
    try {
      const response = await activateKnowledgePack(active);
      await reload();
      // 生效语义以 backend warnings 为准：sidecar 重物化成功=已生效；
      // sidecar 不可达=重启应用后生效。warnings 为空时退回本地文案。
      notify(response.warnings[0] ?? successMessage);
    } catch {
      notify(t("settings.knowledge.switchFailed"));
    }
  };

  // 点官方行=切回官方（停用第三方）；确认弹窗按线框文案。
  const requestActivateOfficial = () => {
    if (!listing || listing.active === "official") return;
    setConfirmAction({
      title: t("settings.knowledge.deactivateTitle"),
      impact: t("settings.knowledge.deactivateImpact"),
      run: () => activate("official", t("settings.knowledge.deactivated")),
    });
  };

  // 点包行=激活；invalid 包不可激活（线框：红点+「校验失败」，点击只提示）。
  const requestActivatePack = (pack: KnowledgePackItemV1) => {
    if (!listing || listing.active === pack.pack_id) return;
    if (!pack.valid) {
      notify(t("settings.knowledge.invalidPack"));
      return;
    }
    setConfirmAction({
      title: t("settings.knowledge.switchTitle"),
      impact: t("settings.knowledge.switchImpact", { name: pack.display_name }),
      run: () => activate(pack.pack_id, t("settings.knowledge.activated", { name: pack.display_name })),
    });
  };

  // 卸载：active 包确认文案明确「自动回退官方知识库」；两者都提示
  // 历史分析 display-only 语义（C3）。
  const requestUninstall = (pack: KnowledgePackItemV1) => {
    const isActive = listing?.active === pack.pack_id;
    setConfirmAction({
      title: isActive ? t("settings.knowledge.uninstallActiveTitle", { name: pack.display_name }) : t("settings.knowledge.uninstallTitle", { name: pack.display_name }),
      impact: isActive
        ? t("settings.knowledge.uninstallActiveImpact")
        : t("settings.knowledge.uninstallImpact"),
      run: async () => {
        try {
          setListing(await uninstallKnowledgePack(pack.pack_id));
          setFallbackDismissed(false);
          notify(isActive ? t("settings.knowledge.uninstalledActive", { name: pack.display_name }) : t("settings.knowledge.uninstalled", { name: pack.display_name }));
        } catch {
          notify(t("settings.knowledge.uninstallFailed"));
        }
      },
    });
  };

  // ── 导入向导（三步模态：①来源与路径 ②校验结果 ③激活确认） ──
  const [wizardOpen, setWizardOpen] = useState(false);
  const [wizardStep, setWizardStep] = useState<WizardStep>(1);
  const [sourceKind, setSourceKind] = useState<SourceKind>("folder");
  const [sourcePath, setSourcePath] = useState("");
  const [importing, setImporting] = useState(false);
  // 第 2 步两态：importResult=校验通过且已安装（待激活）；importRejection=
  // 422 明细上屏并锁「下一步」（失败不写入任何文件）。
  const [importResult, setImportResult] = useState<{
    response: KnowledgePackImportResponseV1;
    pack: KnowledgePackItemV1 | null;
  } | null>(null);
  const [importRejection, setImportRejection] = useState<{ errorCode: string; details: string[] } | null>(null);
  const [activating, setActivating] = useState(false);

  const openWizard = () => {
    setWizardStep(1);
    setSourceKind("folder");
    setSourcePath("");
    setImporting(false);
    setImportResult(null);
    setImportRejection(null);
    setWizardOpen(true);
  };

  const browse = async () => {
    // 文件夹走系统原生选择器（同 KovaaK 本地目录先例）；zip 暂无文件
    // 选择器导出（v1 文本输入，见交付说明）。
    try {
      const path = await pickDesktopDirectory(t("settings.knowledge.browseDialogTitle"));
      if (path) setSourcePath(path);
    } catch {
      notify(t("settings.knowledge.pickerFailed"));
    }
  };

  // 离开第 1 步即触发 import（校验+安装一体，C6）；成功后取最新列表把
  // has_mapping/作者等元信息带给第 3 步摘要，列表刷新失败不阻塞向导。
  const runImport = async () => {
    const trimmed = sourcePath.trim();
    if (!trimmed || importing) return;
    setImporting(true);
    setImportResult(null);
    setImportRejection(null);
    try {
      const response = await importKnowledgePack(trimmed);
      let pack: KnowledgePackItemV1 | null = null;
      try {
        const next = await getKnowledgePacks();
        setListing(next);
        pack = next.packs.find((item) => item.pack_id === response.pack_id) ?? null;
      } catch {
        // 第 3 步摘要退回 import 响应字段。
      }
      setImportResult({ response, pack });
      setWizardStep(2);
    } catch (error) {
      if (error instanceof KnowledgePackImportError) {
        setImportRejection({ errorCode: error.errorCode, details: error.details });
        setWizardStep(2);
        return;
      }
      notify(t("settings.knowledge.importFailed"));
    } finally {
      setImporting(false);
    }
  };

  // 完成并激活：C6 import → activate；安装后直接取消则包保持已装未激活。
  const finishWizard = async () => {
    if (!importResult || activating) return;
    setActivating(true);
    try {
      const response = await activateKnowledgePack(importResult.response.pack_id);
      await reload();
      setWizardOpen(false);
      // 同 activate()：生效语义以 backend warnings 为准（sidecar 不可达时
      // 重启应用后生效），warnings 为空时退回本地文案。
      notify(
        response.warnings[0]
        ?? t("settings.knowledge.installedActivated", { name: importResult.pack?.display_name ?? importResult.response.pack_id }),
      );
    } catch {
      notify(t("settings.knowledge.activateFailed"));
    } finally {
      setActivating(false);
    }
  };

  const wizardBack = () => {
    if (importing || wizardStep <= 1) return;
    // 回到第 1 步即丢弃上次校验结论：路径/类型再变动必须重跑校验。
    setImportResult(null);
    setImportRejection(null);
    setWizardStep((step) => (step - 1) as WizardStep);
  };

  const packs = listing?.packs ?? [];
  const invalidPack = packs.find((pack) => !pack.valid) ?? null;
  // 回退条覆盖两种现状：active=official 但存在坏包；或 active 指针仍指坏包
  // （后端已按官方口径回退，包行「使用中+校验失败」的矛盾由本条消歧）。
  // 用 active 指向的包本体判断，避免多坏包时 find 顺序误判。
  const activePack = packs.find((pack) => pack.pack_id === listing?.active) ?? null;
  const activePackInvalid = activePack !== null && !activePack.valid;
  const showFallbackBar = listing !== null && invalidPack !== null && !fallbackDismissed
    && (listing.active === "official" || activePackInvalid);

  if (!desktop) {
    return (
      <Notice className="task6-settings-notice" tone="warning" title={t("settings.knowledge.desktopOnlyTitle")}>
        {t("settings.knowledge.desktopOnlyBody")}
      </Notice>
    );
  }

  return (
    <>
      <div className="task6-settings-subsection">
        <Panel>
          {loading ? <Loading>{t("settings.knowledge.loading")}</Loading> : null}
          {loadError && !loading ? (
            <Notice className="task6-settings-notice" tone="error" title={t("settings.knowledge.listErrorTitle")}>
              {t("settings.knowledge.listErrorBody")}
              <div className="task6-inline-actions">
                <Button onClick={() => void reload()} size="compact" variant="secondary">{t("common.retry")}</Button>
              </div>
            </Notice>
          ) : null}
          {showFallbackBar && invalidPack ? (
            <Notice className="task6-settings-notice" tone="error" title={t("settings.knowledge.fallbackTitle")}>
              {activePackInvalid
                ? t("settings.knowledge.fallbackActiveInvalid")
                : t("settings.knowledge.fallbackInvalid", { name: invalidPack.display_name })}
              <div className="task6-inline-actions">
                <Button onClick={openWizard} size="compact" variant="ghost">{t("settings.knowledge.reimport")}</Button>
                <Button onClick={() => requestUninstall(invalidPack)} size="compact" variant="ghost">{t("settings.knowledge.uninstallThis")}</Button>
                <Button onClick={() => setFallbackDismissed(true)} size="compact" variant="ghost">{t("settings.knowledge.gotIt")}</Button>
              </div>
            </Notice>
          ) : null}
          {listing ? (
            <>
              <div aria-label={t("settings.knowledge.listAria")} className="task6-provider-list">
                {/* 官方档常驻置顶：点按=切回官方（停用第三方），行尾绿点=有效。 */}
                <div
                  aria-current={listing.active === "official" || undefined}
                  className="task6-provider-list-item"
                  data-official
                  key="official"
                  onClick={requestActivateOfficial}
                  onKeyDown={(event) => { if (event.key === "Enter") requestActivateOfficial(); }}
                  role="button"
                  tabIndex={0}
                  title={listing.active === "official" ? t("settings.knowledge.inUse") : t("settings.knowledge.backToOfficial")}
                >
                  <span className="task6-provider-list-text">
                    <span className="task6-provider-head">
                      <span className="task6-provider-list-name">{t("settings.knowledge.officialName")}</span>
                      {listing.active === "official" ? <Badge tone="neutral">{t("settings.knowledge.inUse")}</Badge> : null}
                    </span>
                    <span className="task6-provider-list-type">{t(OFFICIAL_ROW_SUB_KEY)}</span>
                  </span>
                  <span className="task6-toggle-row-side">
                    <span aria-hidden="true" className="task6-provider-dot" data-ready="true" />
                  </span>
                </div>
                {packs.length > 0 ? (
                  <div aria-hidden="true" style={{ borderTop: "1px solid var(--outline-variant)", margin: "var(--space-2) 0" }} />
                ) : null}
                {packs.map((pack) => {
                  const isActive = listing.active === pack.pack_id;
                  const installedDay = formatInstalledDay(pack.installed_at);
                  return (
                    <div
                      aria-current={isActive || undefined}
                      className="task6-provider-list-item"
                      key={pack.pack_id}
                      onClick={() => requestActivatePack(pack)}
                      onKeyDown={(event) => { if (event.key === "Enter") requestActivatePack(pack); }}
                      role="button"
                      tabIndex={0}
                      title={isActive ? t("settings.knowledge.inUse") : pack.valid ? t("settings.knowledge.switchToPack") : t("settings.knowledge.invalidTitle")}
                    >
                      <span className="task6-provider-list-text">
                        <span className="task6-provider-head">
                          <span className="task6-provider-list-name">{pack.display_name}</span>
                          {pack.has_mapping ? (
                            <Badge title={t("settings.knowledge.mappingBadgeTitle")} tone="info">{t("settings.knowledge.mappingBadge")}</Badge>
                          ) : null}
                          {isActive ? <Badge tone="neutral">{t("settings.knowledge.inUse")}</Badge> : null}
                        </span>
                        <span className="task6-provider-list-type">
                          {pack.author ? `${pack.author} · ` : ""}v{pack.pack_version}
                          {installedDay ? t("settings.knowledge.installedOn", { day: installedDay }) : ""} · {pack.pack_id}
                        </span>
                      </span>
                      <span className="task6-toggle-row-side">
                        {pack.valid ? (
                          <span aria-hidden="true" className="task6-provider-dot" data-ready="true" />
                        ) : (
                          <>
                            <span aria-hidden="true" className="task6-provider-dot" />
                            <span style={{ color: "var(--error)", fontSize: "var(--text-caption)", whiteSpace: "nowrap" }}>{t("settings.knowledge.invalidBadge")}</span>
                          </>
                        )}
                        <Button
                          className="task6-btn-danger-ghost"
                          onClick={(event) => { event.stopPropagation(); requestUninstall(pack); }}
                          size="compact"
                          variant="ghost"
                        >
                          {t("settings.knowledge.uninstall")}
                        </Button>
                      </span>
                    </div>
                  );
                })}
                {packs.length === 0 ? (
                  <p className="task6-muted">{t("settings.knowledge.emptyPacks")}</p>
                ) : null}
              </div>
              <div style={{ alignItems: "flex-start", display: "flex", flexWrap: "wrap", gap: "var(--space-3)", marginTop: "var(--space-3)" }}>
                <Button onClick={openWizard} variant="primary">{t("settings.knowledge.importButton")}</Button>
                <p className="task6-muted" style={{ flex: 1, minWidth: 0 }}>
                  {t("settings.knowledge.importHint")}
                </p>
              </div>
            </>
          ) : null}
        </Panel>
      </div>

      <Dialog
        footer={
          <>
            <Button onClick={() => setConfirmAction(null)} variant="secondary">{t("settings.dialog.cancel")}</Button>
            <Button
              onClick={() => {
                const action = confirmAction;
                setConfirmAction(null);
                void action?.run().catch(() => notify(t("settings.feedback.opIncomplete")));
              }}
              variant="danger"
            >
              {t("settings.dialog.confirm")}
            </Button>
          </>
        }
        onClose={() => setConfirmAction(null)}
        open={Boolean(confirmAction)}
        title={confirmAction?.title ?? t("settings.dialog.confirmTitle")}
      >
        <p>{confirmAction?.impact}</p>
      </Dialog>

      <Dialog
        footer={
          <>
            {wizardStep > 1 ? (
              <Button disabled={importing} onClick={wizardBack} variant="secondary">{t("settings.provider.wizardPrev")}</Button>
            ) : null}
            <span style={{ flex: 1 }} />
            <Button onClick={() => setWizardOpen(false)} variant="secondary">{t("settings.dialog.cancel")}</Button>
            {wizardStep === 1 ? (
              <Button disabled={!sourcePath.trim() || importing} onClick={() => void runImport()} variant="primary">
                {importing ? t("settings.knowledge.checkingInstalling") : t("settings.provider.wizardNext")}
              </Button>
            ) : wizardStep === 2 ? (
              <Button disabled={!importResult} onClick={() => setWizardStep(3)} variant="primary">{t("settings.provider.wizardNext")}</Button>
            ) : (
              <Button disabled={!importResult || activating} onClick={() => void finishWizard()} variant="primary">
                {activating ? t("settings.knowledge.activating") : t("settings.knowledge.finishAndActivate")}
              </Button>
            )}
          </>
        }
        onClose={() => setWizardOpen(false)}
        open={wizardOpen}
        title={t("settings.knowledge.wizardTitle", { step: wizardStep, total: 3 })}
      >
        <ol aria-label={t("settings.knowledge.wizardStepsAria")} className="task6-wizard-steps">
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
          <div className="task6-wizard-step-body">
            <div role="radiogroup" aria-label={t("settings.knowledge.sourceAria")} style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-2)" }}>
              {(Object.keys(SOURCE_KIND_KEYS) as SourceKind[]).map((kind) => (
                <label
                  className="task6-mode-card"
                  data-selected={sourceKind === kind || undefined}
                  key={kind}
                  style={{ alignItems: "flex-start", flex: "1 1 200px", flexDirection: "column", gap: "2px" }}
                >
                  <input checked={sourceKind === kind} name="kb-source-kind" onChange={() => setSourceKind(kind)} type="radio" value={kind} />
                  <span className="task6-mode-card-name">{t(SOURCE_KIND_KEYS[kind].label)}</span>
                  <span className="task6-muted">{t(SOURCE_KIND_KEYS[kind].sub)}</span>
                </label>
              ))}
            </div>
            <div className="task6-form-row">
              <span className="task6-form-row-label">{t("settings.knowledge.pathLabel")}</span>
              <FieldControl
                aria-label={t("settings.knowledge.pathAria")}
                autoComplete="off"
                onChange={(event) => setSourcePath(event.target.value)}
                placeholder={t("settings.knowledge.pathPlaceholder")}
                value={sourcePath}
              />
              {desktop ? (
                <Button disabled={importing} onClick={() => void browse()} size="compact" variant="secondary">{t("settings.knowledge.browse")}</Button>
              ) : null}
            </div>
            <p className="task6-muted">
              {sourceKind === "folder"
                ? t("settings.knowledge.folderHint")
                : t("settings.knowledge.zipHint")}
            </p>
          </div>
        ) : null}

        {wizardStep === 2 ? (
          importRejection ? (
            <div className="task6-wizard-step-body">
              <Notice tone="error" title={t("settings.knowledge.verifyFailedTitle")}>
                {t("settings.knowledge.verifyFailedBody", { n: importRejection.details.length })}
              </Notice>
              <div style={{ display: "grid", gap: "var(--space-2)" }}>
                {importRejection.details.map((detail, index) => (
                  <CheckRow key={index} mark="✗" tone="bad">{detail}</CheckRow>
                ))}
              </div>
            </div>
          ) : importResult ? (
            <div className="task6-wizard-step-body">
              <p className="task6-ok" aria-live="polite">{t("settings.knowledge.verifyPassed")}</p>
              <div style={{ display: "grid", gap: "var(--space-2)" }}>
                <CheckRow mark="✓" tone="ok">
                  manifest.json · coach_knowledge_pack.v1 · {importResult.response.pack_id}@{importResult.response.pack_version}
                </CheckRow>
                <CheckRow mark="✓" tone="ok">
                  {importResult.pack?.has_mapping
                    ? t("settings.knowledge.mappingIncluded")
                    : t("settings.knowledge.knowledgeOnly")}
                </CheckRow>
                {importResult.response.warnings.map((warning) => (
                  <CheckRow key={warning} mark="⚠" tone="warn">{warning}</CheckRow>
                ))}
              </div>
              <p className="task6-muted">{t("settings.knowledge.installedNotActive")}</p>
            </div>
          ) : null
        ) : null}

        {wizardStep === 3 && importResult ? (
          <div className="task6-wizard-step-body">
            <div style={{ background: "var(--surface-container-low)", border: "1px solid var(--outline-variant)", borderRadius: "var(--radius-md)", display: "grid", gap: "var(--space-1)", padding: "var(--space-3) var(--space-4)" }}>
              <div className="task6-form-row">
                <span className="task6-form-row-label">{t("settings.knowledge.nameLabel")}</span>
                <span style={{ fontSize: "var(--text-caption)", fontWeight: 600, minWidth: 0, overflowWrap: "anywhere" }}>
                  {importResult.pack?.display_name ?? importResult.response.pack_id}
                </span>
              </div>
              {importResult.pack?.author ? (
                <div className="task6-form-row">
                  <span className="task6-form-row-label">{t("settings.knowledge.authorLabel")}</span>
                  <span style={{ fontSize: "var(--text-caption)", minWidth: 0, overflowWrap: "anywhere" }}>{importResult.pack.author}</span>
                </div>
              ) : null}
              <div className="task6-form-row">
                <span className="task6-form-row-label">{t("settings.knowledge.versionLabel")}</span>
                <span style={{ fontSize: "var(--text-caption)", minWidth: 0, overflowWrap: "anywhere" }}>
                  {t("settings.knowledge.versionWithId", { version: importResult.response.pack_version, id: importResult.response.pack_id })}
                </span>
              </div>
              <div className="task6-form-row">
                <span className="task6-form-row-label">{t("settings.knowledge.mappingLabel")}</span>
                <span style={{ fontSize: "var(--text-caption)" }}>
                  {importResult.pack?.has_mapping ? t("settings.knowledge.mappingPresent") : t("settings.knowledge.mappingAbsent")}
                </span>
              </div>
              <div className="task6-form-row">
                <span className="task6-form-row-label">{t("settings.knowledge.sourceLabel")}</span>
                <span style={{ fontFamily: "var(--font-mono)", fontSize: "var(--text-micro)", minWidth: 0, overflowWrap: "anywhere" }}>{sourcePath.trim()}</span>
              </div>
            </div>
            <p style={{ fontSize: "var(--text-caption)", lineHeight: 1.6, margin: 0 }}>
              {t("settings.knowledge.activateSummary", { name: importResult.pack?.display_name ?? importResult.response.pack_id })}
            </p>
            <Notice tone="warning" title={t("settings.knowledge.effectiveNoteTitle")}>
              {t("settings.knowledge.effectiveNoteBody")}
            </Notice>
          </div>
        ) : null}
      </Dialog>
    </>
  );
}
