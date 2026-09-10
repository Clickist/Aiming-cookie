"use client";

import { useCallback, useEffect, useRef, useState, type RefObject } from "react";

import { getProviderCatalog, listProviderProfiles, listStoredCustomProviderModels, switchProviderModel } from "@/lib/api";
import { IconCheck, IconChevronDown, IconSpark } from "@/ui/icons";
import type { ProviderCatalogV1, ProviderProfile, ProviderReasoningEffort } from "@/lib/types";

interface CoachModelMenuProps {
  /** Surface a failed switch through the CoachPanel Toast. */
  onError: (message: string) => void;
}

function displayName(model: { model_id: string; model_name?: string } | null | undefined): string | null {
  if (!model) return null;
  return model.model_name && model.model_name.trim() ? model.model_name : model.model_id;
}

// 思考力度档位：value 为空串表示「默认」（未设置 → 运行时对推理模型回落
// 高档）。「关闭」是显式 off，与「默认」语义不同。写回默认档，对下一段
// 回复生效（与切模型同语义）。
const EFFORT_OPTIONS: Array<{ value: ProviderReasoningEffort | ""; label: string }> = [
  { value: "", label: "默认" },
  { value: "off", label: "关闭" },
  { value: "minimal", label: "极简" },
  { value: "low", label: "低" },
  { value: "medium", label: "中" },
  { value: "high", label: "高" },
];

/** 菜单开合的统一关闭路径：点击外部 mousedown 关闭 + Escape 关闭（IME
    守卫，输入法确认候选词期间不消费 Esc）。模型与力度两个独立菜单共用。 */
function useMenuDismiss(ref: RefObject<HTMLElement | null>, open: boolean, onClose: () => void) {
  useEffect(() => {
    if (!open) return undefined;
    const onPointerDown = (event: MouseEvent) => {
      if (ref.current && !ref.current.contains(event.target as Node)) onClose();
    };
    const onKeyDown = (event: KeyboardEvent) => {
      // IME 守卫：输入法确认候选词期间的 Escape 不应关闭菜单。
      if (event.isComposing || event.keyCode === 229) return;
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [ref, open, onClose]);
}

/**
 * Composer 模型与思考力度选择（点点 0910 拍板：拆成两个独立按钮 + 独立
 * 菜单，`… [模型名 ▾] [力度 ▾] [发送]`，交互模式照抄原模型菜单）。两者
 * 共享同一份 profile/catalog 数据（力度挂在默认档上）；切换 Provider 仍然
 * 在 Settings。builtin Providers 需 pinned catalog ≥2 个模型；custom
 * Providers（点点 09-08 拍板）从自己的 /models 列表渲染，≥1 即可。切换
 * 更新全局默认档（sidecar 持久化），按钮反映响应里解析出的模型名。
 *
 * digests §11 item 6：菜单不随运行态连坐 disabled——运行中保持可切换，
 * 选择对下一段回复（下一轮 provider 请求）生效。
 */
export function CoachModelMenu({ onError }: CoachModelMenuProps) {
  const [profile, setProfile] = useState<ProviderProfile | null>(null);
  const [catalog, setCatalog] = useState<ProviderCatalogV1 | null>(null);
  const [modelOpen, setModelOpen] = useState(false);
  const [effortOpen, setEffortOpen] = useState(false);
  const [switching, setSwitching] = useState(false);
  const modelRef = useRef<HTMLDivElement | null>(null);
  const effortRef = useRef<HTMLDivElement | null>(null);

  const loadData = useCallback(async () => {
    try {
      const [profiles, nextCatalog] = await Promise.all([listProviderProfiles(), getProviderCatalog()]);
      // 多档存储下菜单只操作默认档（coach 回合实际解析的档）。
      const active = profiles.profiles.find((entry) => entry.is_default) ?? profiles.profiles[0] ?? null;
      setProfile(active);
      // custom 档不在 builtin 目录里（点点 09-08 拍板：菜单同样可用）：就地
      // 发现该档自己的 /models 列表，key 不出 sidecar；失败=菜单隐藏，不坏 composer。
      if (active && (active.kind === "custom_openai_compatible" || active.kind === "custom_anthropic_compatible")) {
        try {
          const discovered = await listStoredCustomProviderModels(active.id);
          setCatalog({
            providers: [{
              provider_id: active.provider_id,
              provider_name: active.name,
              auth_modes: ["api_key"],
              base_url: active.base_url,
              models: discovered.models.map((model) => ({
                model_id: model.model_id,
                context_window: model.context_window ?? undefined,
                max_tokens: model.max_tokens ?? undefined,
              })),
            }],
          });
        } catch {
          setCatalog(null);
        }
      } else {
        setCatalog(nextCatalog);
      }
      return true;
    } catch {
      // The picker is an enhancement; a sidecar hiccup must not break the composer.
      return false;
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    void loadData().then((loaded) => {
      if (!cancelled && !loaded) {
        setProfile(null);
        setCatalog(null);
      }
    });
    return () => { cancelled = true; };
  }, [loadData]);

  const closeModel = useCallback(() => setModelOpen(false), []);
  const closeEffort = useCallback(() => setEffortOpen(false), []);
  useMenuDismiss(modelRef, modelOpen, closeModel);
  useMenuDismiss(effortRef, effortOpen, closeEffort);

  // 开启时刷新，让 Settings 里改过的 Provider/Model/力度即时反映。
  const toggleModel = () => {
    if (modelOpen) {
      closeModel();
      return;
    }
    void loadData().then((loaded) => { if (loaded) setModelOpen(true); });
  };

  const toggleEffort = () => {
    if (effortOpen) {
      closeEffort();
      return;
    }
    void loadData().then((loaded) => { if (loaded) setEffortOpen(true); });
  };

  const models = profile
    ? (catalog?.providers.find((entry) => entry.provider_id === profile.provider_id)?.models ?? [])
    : [];
  // builtin 沿用 0827 门槛（目录 ≥2 才有切换意义）；custom 档 ≥1 即渲染——
  // 单模型也值得显示当前模型名（点点 09-08：不再对 custom 档设多余限制）。
  if (!profile) return null;
  const minModels = profile.kind === "builtin" ? 2 : 1;
  if (models.length < minModels) return null;

  const activeModelId = profile.model_id;
  const currentModel = models.find((model) => model.model_id === activeModelId) ?? null;
  const currentName = displayName(currentModel) ?? profile.model_id;

  const handleSelect = async (modelId: string) => {
    if (modelId === activeModelId || switching) return;
    setSwitching(true);
    try {
      const status = await switchProviderModel(modelId);
      const resolvedId = status.model?.model_id ?? modelId;
      // Adopt the sidecar-resolved model id; the catalog maps it to the name.
      setProfile((current) => (current ? { ...current, model_id: resolvedId } : current));
      closeModel();
    } catch (error) {
      onError(error instanceof Error && error.message.trim() ? error.message : "模型切换失败，请重试。");
    } finally {
      setSwitching(false);
    }
  };

  // 力度挂在默认档上：model_id 传当前值即「只改力度」。null 表示清回默认。
  const activeEffort = profile.reasoning_effort ?? "";
  const currentEffortLabel = EFFORT_OPTIONS.find((option) => option.value === activeEffort)?.label ?? "默认";
  const handleEffortSelect = async (effort: ProviderReasoningEffort | "") => {
    if (switching) return;
    const nextEffort: ProviderReasoningEffort | null = effort === "" ? null : effort;
    if ((profile.reasoning_effort ?? null) === nextEffort) return;
    setSwitching(true);
    try {
      await switchProviderModel(activeModelId, { reasoningEffort: nextEffort });
      setProfile((current) => (current ? { ...current, reasoning_effort: nextEffort } : current));
      closeEffort();
    } catch (error) {
      onError(error instanceof Error && error.message.trim() ? error.message : "思考力度调整失败，请重试。");
    } finally {
      setSwitching(false);
    }
  };

  // 力度按钮只在当前模型确认支持推理时出现（目录元数据）。
  const showEffortSection = currentModel?.reasoning === true;

  return (
    <>
      <div className="task6-composer-model-wrap" ref={modelRef}>
        {/* 模型名完整显示（点点 0910 拍板：不截断不缩写；重度挤压才允许
            ellipsis，见 task6.css 的 container query 分级）。 */}
        <button
          aria-expanded={modelOpen}
          aria-haspopup="menu"
          className="task6-composer-model"
          disabled={switching}
          onClick={toggleModel}
          title="切换模型（对下一段回复生效）"
          type="button"
        >
          <span className="task6-composer-model-label">{currentName}</span>
          <IconChevronDown className="task6-composer-model-caret" />
        </button>
        {modelOpen ? (
          <div aria-label="当前 Provider 的模型" className="task6-composer-model-menu" role="menu">
            {models.map((model) => {
              const selected = model.model_id === activeModelId;
              return (
                <button
                  aria-checked={selected}
                  className="task6-composer-model-item"
                  key={model.model_id}
                  onClick={() => void handleSelect(model.model_id)}
                  role="menuitemradio"
                  type="button"
                >
                  <span>{displayName(model) ?? model.model_id}</span>
                  {selected ? <IconCheck className="task6-composer-model-check" /> : null}
                </button>
              );
            })}
          </div>
        ) : null}
      </div>
      {showEffortSection ? (
        <div className="task6-composer-effort-wrap" ref={effortRef}>
          {/* 常态显示文字档位；挤压时文字收进 IconSpark 图标（CSS container
              query 分级切换，永不消失按钮本身）。 */}
          <button
            aria-expanded={effortOpen}
            aria-haspopup="menu"
            aria-label="思考力度"
            className="task6-composer-effort"
            disabled={switching}
            onClick={toggleEffort}
            title={`思考力度：${currentEffortLabel}（对下一段回复生效）`}
            type="button"
          >
            <span className="task6-composer-effort-label">{currentEffortLabel}</span>
            <IconSpark className="task6-composer-effort-glyph" />
            <IconChevronDown className="task6-composer-model-caret" />
          </button>
          {effortOpen ? (
            <div aria-label="思考力度（对下一段回复生效）" className="task6-composer-effort-menu" role="menu">
              {EFFORT_OPTIONS.map((option) => {
                const selected = activeEffort === option.value;
                return (
                  <button
                    aria-checked={selected}
                    className="task6-composer-model-item"
                    key={option.value || "default"}
                    onClick={() => void handleEffortSelect(option.value)}
                    role="menuitemradio"
                    type="button"
                  >
                    <span>{option.label}</span>
                    {selected ? <IconCheck className="task6-composer-model-check" /> : null}
                  </button>
                );
              })}
            </div>
          ) : null}
        </div>
      ) : null}
    </>
  );
}
