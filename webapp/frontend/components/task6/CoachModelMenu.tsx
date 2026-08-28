"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { getProviderCatalog, listProviderProfiles, switchProviderModel } from "@/lib/api";
import { IconCheck, IconChevronDown } from "@/ui/icons";
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

/**
 * Composer model picker. Only renders for a builtin Provider whose pinned
 * catalog offers at least two models; switching Provider still lives in
 * Settings. A switch updates the global default profile (persisted by the
 * sidecar) and the button reflects the resolved model name from the response.
 *
 * digests §11 item 6：菜单不随运行态连坐 disabled——运行中保持可切换，
 * 选择对下一段回复（下一轮 provider 请求）生效。
 */
export function CoachModelMenu({ onError }: CoachModelMenuProps) {
  const [profile, setProfile] = useState<ProviderProfile | null>(null);
  const [catalog, setCatalog] = useState<ProviderCatalogV1 | null>(null);
  const [open, setOpen] = useState(false);
  const [switching, setSwitching] = useState(false);
  const containerRef = useRef<HTMLDivElement | null>(null);

  const loadData = useCallback(async () => {
    try {
      const [profiles, nextCatalog] = await Promise.all([listProviderProfiles(), getProviderCatalog()]);
      // 多档存储下菜单只操作默认档（coach 回合实际解析的档）。
      setProfile(profiles.profiles.find((entry) => entry.is_default) ?? profiles.profiles[0] ?? null);
      setCatalog(nextCatalog);
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

  const toggleOpen = () => {
    if (open) {
      setOpen(false);
      return;
    }
    // Refresh on open so a Provider/Model change made in Settings is reflected.
    void loadData().then((loaded) => { if (loaded) setOpen(true); });
  };

  useEffect(() => {
    if (!open) return undefined;
    const onPointerDown = (event: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    const onKeyDown = (event: KeyboardEvent) => {
      // IME 守卫：输入法确认候选词期间的 Escape 不应关闭菜单。
      if (event.isComposing || event.keyCode === 229) return;
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  const models = profile
    ? (catalog?.providers.find((entry) => entry.provider_id === profile.provider_id)?.models ?? [])
    : [];
  if (!profile || profile.kind !== "builtin" || models.length < 2) return null;

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
      setOpen(false);
    } catch (error) {
      onError(error instanceof Error && error.message.trim() ? error.message : "模型切换失败，请重试。");
    } finally {
      setSwitching(false);
    }
  };

  // 力度挂在默认档上：model_id 传当前值即「只改力度」。null 表示清回默认。
  const activeEffort = profile.reasoning_effort ?? "";
  const handleEffortSelect = async (effort: ProviderReasoningEffort | "") => {
    if (switching) return;
    const nextEffort: ProviderReasoningEffort | null = effort === "" ? null : effort;
    if ((profile.reasoning_effort ?? null) === nextEffort) return;
    setSwitching(true);
    try {
      await switchProviderModel(activeModelId, { reasoningEffort: nextEffort });
      setProfile((current) => (current ? { ...current, reasoning_effort: nextEffort } : current));
    } catch (error) {
      onError(error instanceof Error && error.message.trim() ? error.message : "思考力度调整失败，请重试。");
    } finally {
      setSwitching(false);
    }
  };

  // 力度段只在当前模型确认支持推理时出现（目录元数据）。
  const showEffortSection = currentModel?.reasoning === true;

  return (
    <div className="task6-composer-model-wrap" ref={containerRef}>
      <button
        aria-expanded={open}
        aria-haspopup="menu"
        className="task6-composer-model"
        disabled={switching}
        onClick={toggleOpen}
        title="切换模型（对下一段回复生效）"
        type="button"
      >
        <span className="task6-composer-model-label">{currentName}</span>
        <IconChevronDown className="task6-composer-model-caret" />
      </button>
      {open ? (
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
          {showEffortSection ? (
            <>
              {/* task6.css 本特性禁改：分隔与段标题用 token 内联样式。 */}
              <div role="separator" style={{ borderTop: "1px solid var(--outline-variant)", margin: "var(--space-1) 0" }} />
              <div
                aria-label="思考力度"
                role="group"
                style={{
                  padding: "var(--space-1) var(--space-3)",
                  color: "var(--on-surface-variant)",
                  font: "500 var(--text-ui)/1.3 var(--font-ui)",
                }}
              >
                思考力度（对下一段回复生效）
              </div>
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
            </>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
