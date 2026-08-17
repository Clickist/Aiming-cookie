"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { getProviderCatalog, listProviderProfiles, switchProviderModel } from "@/lib/api";
import type { ProviderCatalogV1, ProviderProfile } from "@/lib/types";

interface CoachModelMenuProps {
  /** Disable switching while an agent run is queued/running. */
  disabled: boolean;
  /** Surface a failed switch through the CoachPanel Toast. */
  onError: (message: string) => void;
}

function displayName(model: { model_id: string; model_name?: string } | null | undefined): string | null {
  if (!model) return null;
  return model.model_name && model.model_name.trim() ? model.model_name : model.model_id;
}

/**
 * Composer model picker. Only renders for a builtin Provider whose pinned
 * catalog offers at least two models; switching Provider still lives in
 * Settings. A switch updates the global default profile (persisted by the
 * sidecar) and the button reflects the resolved model name from the response.
 */
export function CoachModelMenu({ disabled, onError }: CoachModelMenuProps) {
  const [profile, setProfile] = useState<ProviderProfile | null>(null);
  const [catalog, setCatalog] = useState<ProviderCatalogV1 | null>(null);
  const [open, setOpen] = useState(false);
  const [switching, setSwitching] = useState(false);
  const containerRef = useRef<HTMLDivElement | null>(null);

  const loadData = useCallback(async () => {
    try {
      const [profiles, nextCatalog] = await Promise.all([listProviderProfiles(), getProviderCatalog()]);
      setProfile(profiles.profiles[0] ?? null);
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
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  useEffect(() => {
    if (disabled) setOpen(false);
  }, [disabled]);

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

  return (
    <div className="task6-composer-model-wrap" ref={containerRef}>
      <button
        aria-expanded={open}
        aria-haspopup="menu"
        className="task6-composer-model"
        disabled={disabled || switching}
        onClick={toggleOpen}
        title="切换模型"
        type="button"
      >
        <span className="task6-composer-model-label">{currentName}</span>
        <span aria-hidden="true" className="task6-composer-model-caret">▾</span>
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
                {selected ? <span aria-hidden="true" className="task6-composer-model-check">✓</span> : null}
              </button>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}
