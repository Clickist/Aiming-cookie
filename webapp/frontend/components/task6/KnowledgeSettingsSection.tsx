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

const WIZARD_STEP_LABELS = ["选路径", "校验结果", "激活确认"] as const;

const OFFICIAL_ROW_SUB = "内置 · 随产品更新 · 官方训练知识与判定规则";

const SOURCE_KIND_DETAILS: Record<SourceKind, { label: string; sub: string }> = {
  folder: { label: "本地文件夹", sub: "manifest.json + knowledge/ + mapping.json（可选）" },
  zip: { label: "zip 压缩包", sub: "安全解包（防路径穿越、大小上限）后同上校验" },
};

/** 「9月18日」短日期；解析失败不硬造（不渲染该片段）。 */
function formatInstalledDay(iso: string): string | null {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return null;
  return new Intl.DateTimeFormat("zh-CN", { month: "long", day: "numeric" }).format(date);
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
      notify("知识库切换未完成，未伪造成功状态，请重试。");
    }
  };

  // 点官方行=切回官方（停用第三方）；确认弹窗按线框文案。
  const requestActivateOfficial = () => {
    if (!listing || listing.active === "official") return;
    setConfirmAction({
      title: "停用第三方知识库",
      impact: "将切回 Aiming Cookie 官方知识库，切换后立即生效（下一次对话使用官方口径）；若 Coach 引擎不可达，则重启应用后生效。历史分析保持各自当时使用的知识库口径，不受影响。",
      run: () => activate("official", "已切回官方知识库，下一次对话即使用官方口径。"),
    });
  };

  // 点包行=激活；invalid 包不可激活（线框：红点+「校验失败」，点击只提示）。
  const requestActivatePack = (pack: KnowledgePackItemV1) => {
    if (!listing || listing.active === pack.pack_id) return;
    if (!pack.valid) {
      notify("该包校验失败，无法激活；请修复后重新导入。");
      return;
    }
    setConfirmAction({
      title: "切换知识库",
      impact: `激活后 Coach 将改用《${pack.display_name}》的口径分析与讲解，切换后立即生效（下一次对话使用新知识库口径）；若 Coach 引擎不可达，则重启应用后生效。当前进行中的分析不受影响。`,
      run: () => activate(pack.pack_id, `已激活《${pack.display_name}》，下一次对话即使用新知识库口径。`),
    });
  };

  // 卸载：active 包确认文案明确「自动回退官方知识库」；两者都提示
  // 历史分析 display-only 语义（C3）。
  const requestUninstall = (pack: KnowledgePackItemV1) => {
    const isActive = listing?.active === pack.pack_id;
    setConfirmAction({
      title: isActive ? `卸载使用中的《${pack.display_name}》` : `卸载《${pack.display_name}》`,
      impact: isActive
        ? "该包当前使用中：卸载后将自动回退 Aiming Cookie 官方知识库（完全生效需重启应用）。包文件将从本机移除；历史分析仍可打开，其中引用该包的知识条目将显示「来自已移除的知识库」。"
        : "此包将从本机移除。历史分析仍可打开，其中引用该包的知识条目将显示「来自已移除的知识库」。",
      run: async () => {
        try {
          setListing(await uninstallKnowledgePack(pack.pack_id));
          setFallbackDismissed(false);
          notify(isActive ? `已卸载《${pack.display_name}》并回退官方知识库。` : `已卸载《${pack.display_name}》。`);
        } catch {
          notify("卸载未完成，未伪造成功状态，请重试。");
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
      const path = await pickDesktopDirectory("选择知识包文件夹");
      if (path) setSourcePath(path);
    } catch {
      notify("无法打开文件夹选择器，请直接粘贴完整路径。");
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
      notify("导入未完成：本地服务不可用或路径无法读取，请重试。");
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
        ?? `已安装并激活《${importResult.pack?.display_name ?? importResult.response.pack_id}》，下一次对话即使用新知识库口径。`,
      );
    } catch {
      notify("激活未完成，未伪造成功状态，请重试。");
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
      <Notice className="task6-settings-notice" tone="warning" title="仅限桌面版">
        知识包导入与切换依赖本机服务与系统文件夹选择器；浏览器预览不提供。
      </Notice>
    );
  }

  return (
    <>
      <div className="task6-settings-subsection">
        <Panel>
          {loading ? <Loading>正在读取知识包</Loading> : null}
          {loadError && !loading ? (
            <Notice className="task6-settings-notice" tone="error" title="知识包列表暂时无法读取">
              请检查本地服务后重试；已保留上次读取到的内容。
              <div className="task6-inline-actions">
                <Button onClick={() => void reload()} size="compact" variant="secondary">重试</Button>
              </div>
            </Notice>
          ) : null}
          {showFallbackBar && invalidPack ? (
            <Notice className="task6-settings-notice" tone="error" title="已自动回退官方知识库">
              {activePackInvalid
                ? "当前激活的知识包校验未通过，已自动改用官方知识库口径；可重新导入或卸载该包。"
                : `《${invalidPack.display_name}》当前校验未通过；本次分析与 Coach 会话使用官方知识库口径。修复后重新导入，或卸载该包。`}
              <div className="task6-inline-actions">
                <Button onClick={openWizard} size="compact" variant="ghost">重新导入…</Button>
                <Button onClick={() => requestUninstall(invalidPack)} size="compact" variant="ghost">卸载该包…</Button>
                <Button onClick={() => setFallbackDismissed(true)} size="compact" variant="ghost">知道了</Button>
              </div>
            </Notice>
          ) : null}
          {listing ? (
            <>
              <div aria-label="知识包列表" className="task6-provider-list">
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
                  title={listing.active === "official" ? "当前使用中" : "点击切回官方知识库（停用第三方）"}
                >
                  <span className="task6-provider-list-text">
                    <span className="task6-provider-head">
                      <span className="task6-provider-list-name">Aiming Cookie 官方</span>
                      {listing.active === "official" ? <Badge tone="neutral">使用中</Badge> : null}
                    </span>
                    <span className="task6-provider-list-type">{OFFICIAL_ROW_SUB}</span>
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
                      title={isActive ? "当前使用中" : pack.valid ? "点击切换到此知识包" : "校验失败，不可激活"}
                    >
                      <span className="task6-provider-list-text">
                        <span className="task6-provider-head">
                          <span className="task6-provider-list-name">{pack.display_name}</span>
                          {pack.has_mapping ? (
                            <Badge title="含 mapping 判定规则引擎数据：诊断判定与讲解口径都来自此包" tone="info">判定规则</Badge>
                          ) : null}
                          {isActive ? <Badge tone="neutral">使用中</Badge> : null}
                        </span>
                        <span className="task6-provider-list-type">
                          {pack.author ? `${pack.author} · ` : ""}v{pack.pack_version}
                          {installedDay ? ` · ${installedDay}安装` : ""} · {pack.pack_id}
                        </span>
                      </span>
                      <span className="task6-toggle-row-side">
                        {pack.valid ? (
                          <span aria-hidden="true" className="task6-provider-dot" data-ready="true" />
                        ) : (
                          <>
                            <span aria-hidden="true" className="task6-provider-dot" />
                            <span style={{ color: "var(--error)", fontSize: "var(--text-caption)", whiteSpace: "nowrap" }}>校验失败</span>
                          </>
                        )}
                        <Button
                          className="task6-btn-danger-ghost"
                          onClick={(event) => { event.stopPropagation(); requestUninstall(pack); }}
                          size="compact"
                          variant="ghost"
                        >
                          卸载
                        </Button>
                      </span>
                    </div>
                  );
                })}
                {packs.length === 0 ? (
                  <p className="task6-muted">还没有安装第三方知识包；导入后点按包行即可切换。</p>
                ) : null}
              </div>
              <div style={{ alignItems: "flex-start", display: "flex", flexWrap: "wrap", gap: "var(--space-3)", marginTop: "var(--space-3)" }}>
                <Button onClick={openWizard} variant="primary">＋ 导入知识包</Button>
                <p className="task6-muted" style={{ flex: 1, minWidth: 0 }}>
                  v1 仅支持本地导入（本地文件夹或 .zip），安装时整包校验、失败不写入任何文件；场景库始终使用官方版本，不随包替换。含「判定规则」徽标的包带有 mapping 判定规则引擎数据，除知识讲解外也接管诊断判定口径。
                </p>
              </div>
            </>
          ) : null}
        </Panel>
      </div>

      <Dialog
        footer={
          <>
            <Button onClick={() => setConfirmAction(null)} variant="secondary">取消</Button>
            <Button
              onClick={() => {
                const action = confirmAction;
                setConfirmAction(null);
                void action?.run().catch(() => notify("操作未完成，未伪造成功状态，请重试。"));
              }}
              variant="danger"
            >
              确认
            </Button>
          </>
        }
        onClose={() => setConfirmAction(null)}
        open={Boolean(confirmAction)}
        title={confirmAction?.title ?? "确认操作"}
      >
        <p>{confirmAction?.impact}</p>
      </Dialog>

      <Dialog
        footer={
          <>
            {wizardStep > 1 ? (
              <Button disabled={importing} onClick={wizardBack} variant="secondary">上一步</Button>
            ) : null}
            <span style={{ flex: 1 }} />
            <Button onClick={() => setWizardOpen(false)} variant="secondary">取消</Button>
            {wizardStep === 1 ? (
              <Button disabled={!sourcePath.trim() || importing} onClick={() => void runImport()} variant="primary">
                {importing ? "校验并安装中…" : "下一步"}
              </Button>
            ) : wizardStep === 2 ? (
              <Button disabled={!importResult} onClick={() => setWizardStep(3)} variant="primary">下一步</Button>
            ) : (
              <Button disabled={!importResult || activating} onClick={() => void finishWizard()} variant="primary">
                {activating ? "正在激活…" : "完成并激活"}
              </Button>
            )}
          </>
        }
        onClose={() => setWizardOpen(false)}
        open={wizardOpen}
        title={`导入知识包 · 第 ${wizardStep}/3 步`}
      >
        <ol aria-label="导入向导步骤" className="task6-wizard-steps">
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
          <div className="task6-wizard-step-body">
            <div role="radiogroup" aria-label="来源类型" style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-2)" }}>
              {(Object.keys(SOURCE_KIND_DETAILS) as SourceKind[]).map((kind) => (
                <label
                  className="task6-mode-card"
                  data-selected={sourceKind === kind || undefined}
                  key={kind}
                  style={{ alignItems: "flex-start", flex: "1 1 200px", flexDirection: "column", gap: "2px" }}
                >
                  <input checked={sourceKind === kind} name="kb-source-kind" onChange={() => setSourceKind(kind)} type="radio" value={kind} />
                  <span className="task6-mode-card-name">{SOURCE_KIND_DETAILS[kind].label}</span>
                  <span className="task6-muted">{SOURCE_KIND_DETAILS[kind].sub}</span>
                </label>
              ))}
            </div>
            <div className="task6-form-row">
              <span className="task6-form-row-label">包路径</span>
              <FieldControl
                aria-label="知识包路径"
                autoComplete="off"
                onChange={(event) => setSourcePath(event.target.value)}
                placeholder="选择或粘贴本地文件夹 / .zip 的完整路径"
                value={sourcePath}
              />
              {desktop ? (
                <Button disabled={importing} onClick={() => void browse()} size="compact" variant="secondary">浏览…</Button>
              ) : null}
            </div>
            <p className="task6-muted">
              {sourceKind === "folder"
                ? "点「浏览…」用系统选择器选择包文件夹；也可以直接粘贴完整路径。"
                : "粘贴 .zip 压缩包的完整路径；安装前会做防路径穿越与大小上限的安全解包校验。"}
            </p>
          </div>
        ) : null}

        {wizardStep === 2 ? (
          importRejection ? (
            <div className="task6-wizard-step-body">
              <Notice tone="error" title="校验未通过，未安装任何文件">
                {importRejection.details.length} 处错误。修复后重新导入即可；本次导入不写入任何文件（校验在安装前完成）。
              </Notice>
              <div style={{ display: "grid", gap: "var(--space-2)" }}>
                {importRejection.details.map((detail, index) => (
                  <CheckRow key={index} mark="✗" tone="bad">{detail}</CheckRow>
                ))}
              </div>
            </div>
          ) : importResult ? (
            <div className="task6-wizard-step-body">
              <p className="task6-ok" aria-live="polite">✓ 校验通过，可以激活</p>
              <div style={{ display: "grid", gap: "var(--space-2)" }}>
                <CheckRow mark="✓" tone="ok">
                  manifest.json · coach_knowledge_pack.v1 · {importResult.response.pack_id}@{importResult.response.pack_version}
                </CheckRow>
                <CheckRow mark="✓" tone="ok">
                  {importResult.pack?.has_mapping
                    ? "判定规则 · 包含 mapping 判定规则数据，诊断判定口径随包生效"
                    : "纯知识口径包（不含 mapping 判定规则）"}
                </CheckRow>
                {importResult.response.warnings.map((warning) => (
                  <CheckRow key={warning} mark="⚠" tone="warn">{warning}</CheckRow>
                ))}
              </div>
              <p className="task6-muted">包已安装登记但尚未激活；下一步确认后启用。</p>
            </div>
          ) : null
        ) : null}

        {wizardStep === 3 && importResult ? (
          <div className="task6-wizard-step-body">
            <div style={{ background: "var(--surface-container-low)", border: "1px solid var(--outline-variant)", borderRadius: "var(--radius-md)", display: "grid", gap: "var(--space-1)", padding: "var(--space-3) var(--space-4)" }}>
              <div className="task6-form-row">
                <span className="task6-form-row-label">名称</span>
                <span style={{ fontSize: "var(--text-caption)", fontWeight: 600, minWidth: 0, overflowWrap: "anywhere" }}>
                  {importResult.pack?.display_name ?? importResult.response.pack_id}
                </span>
              </div>
              {importResult.pack?.author ? (
                <div className="task6-form-row">
                  <span className="task6-form-row-label">作者</span>
                  <span style={{ fontSize: "var(--text-caption)", minWidth: 0, overflowWrap: "anywhere" }}>{importResult.pack.author}</span>
                </div>
              ) : null}
              <div className="task6-form-row">
                <span className="task6-form-row-label">版本</span>
                <span style={{ fontSize: "var(--text-caption)", minWidth: 0, overflowWrap: "anywhere" }}>
                  {importResult.response.pack_version}（{importResult.response.pack_id}）
                </span>
              </div>
              <div className="task6-form-row">
                <span className="task6-form-row-label">判定规则</span>
                <span style={{ fontSize: "var(--text-caption)" }}>
                  {importResult.pack?.has_mapping ? "含 mapping 判定规则数据" : "不含 mapping"}
                </span>
              </div>
              <div className="task6-form-row">
                <span className="task6-form-row-label">来源</span>
                <span style={{ fontFamily: "var(--font-mono)", fontSize: "var(--text-micro)", minWidth: 0, overflowWrap: "anywhere" }}>{sourcePath.trim()}</span>
              </div>
            </div>
            <p style={{ fontSize: "var(--text-caption)", lineHeight: 1.6, margin: 0 }}>
              激活后，Coach 的诊断与讲解将改用《{importResult.pack?.display_name ?? importResult.response.pack_id}》的口径。历史分析按各自当时使用的知识库保留，不受影响。
            </p>
            <Notice tone="warning" title="切换生效说明">
              切换后立即生效，下一次对话即使用新知识库口径；若 Coach 引擎不可达，则重启应用后生效。进行中的分析保持其当时的口径。
            </Notice>
          </div>
        ) : null}
      </Dialog>
    </>
  );
}
