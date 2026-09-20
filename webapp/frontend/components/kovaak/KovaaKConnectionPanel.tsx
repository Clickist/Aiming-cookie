"use client";

import { useCallback, useEffect, useState } from "react";

import {
  deleteKovaaKConnection,
  getKovaaKConnection,
  getKovaaKScores,
  refreshKovaaKConnection,
  saveKovaaKConnection,
} from "@/lib/api";
import { getLocale, useT } from "@/lib/i18n";
import type { KovaaKScoresV1 } from "@/lib/types";
import { Button, Dialog, Field, FieldControl, Notice, Status } from "@/ui/primitives";

type PanelContext = "onboarding" | "settings";
type Operation = "idle" | "loading" | "saving" | "refreshing" | "removing";
type FeedbackTone = "info" | "warning" | "error" | "success";

interface KovaaKConnectionPanelProps {
  context: PanelContext;
  onContinue?: () => void;
  onSkip?: () => void;
}

interface Feedback {
  tone: FeedbackTone;
  message: string;
}

const STEAM_ID = /^\d{17}$/;
const STEAM_PROFILE = /^https:\/\/steamcommunity\.com\/profiles\/\d{17}\/$/;

function isSteamProfile(value: string): boolean {
  return STEAM_ID.test(value) || STEAM_PROFILE.test(value);
}

export function KovaaKConnectionPanel({ context, onContinue, onSkip }: KovaaKConnectionPanelProps) {
  const t = useT();
  const [connected, setConnected] = useState(false);
  const [scores, setScores] = useState<KovaaKScoresV1 | null>(null);
  const [steamProfile, setSteamProfile] = useState("");
  const [identityConsent, setIdentityConsent] = useState(false);
  const [inputError, setInputError] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<Feedback | null>(null);
  const [operation, setOperation] = useState<Operation>("loading");
  const [confirmRemove, setConfirmRemove] = useState(false);

  const hasScores = scores?.availability === "available";

  const observedAt = useCallback((value: string | null): string => {
    if (!value) return t("kovaak.connection.observedNone");
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return t("kovaak.connection.observedFallback");
    // 时间格式跟随当前 locale（批 1 formatHistoryDate 同款改法）。
    return new Intl.DateTimeFormat(getLocale() === "en-US" ? "en-US" : "zh-CN", { dateStyle: "medium", timeStyle: "short" }).format(date);
  }, [t]);

  const load = useCallback(async () => {
    setOperation("loading");
    const [connectionResult, scoresResult] = await Promise.allSettled([
      getKovaaKConnection(),
      getKovaaKScores(),
    ]);
    if (connectionResult.status === "fulfilled") {
      setConnected(connectionResult.value.connected);
    }
    if (scoresResult.status === "fulfilled") {
      setScores(scoresResult.value);
    }
    if (connectionResult.status === "rejected") {
      setFeedback({ tone: "error", message: t("kovaak.connection.readFailed") });
    } else if (scoresResult.status === "rejected") {
      setFeedback({ tone: "error", message: t("kovaak.connection.scoresReadFailed") });
    } else {
      setFeedback(null);
    }
    setOperation("idle");
  }, [t]);

  useEffect(() => {
    void load();
  }, [load]);

  const save = async () => {
    const value = steamProfile.trim();
    if (!isSteamProfile(value)) {
      setInputError(t("kovaak.connection.steamIdInvalid"));
      return;
    }
    // 点点 0912 拍板：settings 去掉同意勾选门槛（愿意输入并点读取即表达意图）；
    // onboarding 向导仍保留勾选。后端合同 identity_consent 恒为 true。
    if (context === "onboarding" && !identityConsent) {
      setInputError(t("kovaak.connection.consentRequired"));
      return;
    }
    setOperation("saving");
    setInputError(null);
    setFeedback(null);
    try {
      await saveKovaaKConnection({ steam_profile: value, identity_consent: true });
      setConnected(true);
      setSteamProfile("");
      await refresh();
    } catch {
      setFeedback({ tone: "error", message: t("kovaak.connection.saveFailed") });
      setOperation("idle");
    }
  };

  const refresh = async () => {
    setOperation("refreshing");
    setFeedback(null);
    try {
      await refreshKovaaKConnection();
      const nextScores = await getKovaaKScores();
      setScores(nextScores);
      setFeedback({ tone: "success", message: t("kovaak.connection.scoresUpdated") });
    } catch {
      setFeedback({
        tone: hasScores ? "warning" : "error",
        message: hasScores ? t("kovaak.connection.refreshNoUpdate") : t("kovaak.connection.refreshNoScores"),
      });
    } finally {
      setOperation("idle");
    }
  };

  const remove = async () => {
    setOperation("removing");
    setFeedback(null);
    try {
      await deleteKovaaKConnection();
      setConnected(false);
      setScores(null);
      setIdentityConsent(false);
      setFeedback({ tone: "success", message: t("kovaak.connection.removed") });
    } catch {
      setFeedback({ tone: "error", message: t("kovaak.connection.removeFailed") });
    } finally {
      setConfirmRemove(false);
      setOperation("idle");
    }
  };

  const busy = operation !== "idle";
  // 成功反馈用裸绿字（点点 0912：不要带框的 Status 壳）。
  const feedbackMessage = feedback
    ? feedback.tone === "success"
      ? <span className="task6-ok">{feedback.message}</span>
      : <Notice tone={feedback.tone}>{feedback.message}</Notice>
    : null;

  /* ── 状态 4：正在读取（局部骨架，不阻塞其它操作） ───────────── */
  if (operation === "loading") {
    return (
      <div className="kovaak-panel" data-context={context}>
        <div className="kovaak-module">
          <div className="kovaak-module-read-state">
            <Status tone="neutral"><span className="kovaak-skeleton-dot" />{t("kovaak.connection.loading")}</Status>
            <span className="kovaak-module-note">{t("kovaak.connection.loadingHint")}</span>
          </div>
          <div style={{ marginTop: "var(--space-3)", width: "62%" }}><div className="kovaak-skeleton" /></div>
          <div style={{ marginTop: "var(--space-2)", width: "44%" }}><div className="kovaak-skeleton" /></div>
        </div>
      </div>
    );
  }

  /* ── 状态 1–3：未连接 / URL 无效 / 尚未同意 ─────────────────── */
  // 线框形态：输入与「读取成绩」同一行，按钮在行尾。
  const connectRow = (
    <div className="kovaak-connect-row">
      <FieldControl
        data-invalid={inputError ? "true" : undefined}
        onChange={(event) => { setSteamProfile(event.target.value); setInputError(null); }}
        placeholder={t("kovaak.connection.inputPlaceholder")}
        value={steamProfile}
      />
      <Button disabled={busy || (context === "onboarding" && !identityConsent)} onClick={() => void save()}>
        {operation === "saving" ? t("kovaak.connection.reading") : t("kovaak.connection.readScores")}
      </Button>
    </div>
  );
  const connectModule = (
    <div className="kovaak-module">
      {context === "settings" ? (
        <>
          {/* 0912 线框拍板：卡内自包含标题；同意勾选与说明文字全部退役。 */}
          <h3 className="task6-profile-group-title">{t("kovaak.connection.settingsTitle")}</h3>
          <p className="task6-card-desc">{t("kovaak.connection.settingsDesc")}</p>
        </>
      ) : null}
      <div className="kovaak-connect-form">
        {context === "onboarding" ? <Field label={t("kovaak.connection.steamFieldLabel")}>{connectRow}</Field> : connectRow}
        {context === "onboarding" ? (
          <>
            <p className="kovaak-module-note">{t("kovaak.connection.pasteHint")}</p>
            <label className="kovaak-consent">
              <input
                checked={identityConsent}
                onChange={(event) => setIdentityConsent(event.target.checked)}
                type="checkbox"
              />
              <span>{t("kovaak.connection.consentLabel")}</span>
            </label>
            <p className="kovaak-module-note">
              {t("kovaak.connection.consentNote")}
            </p>
          </>
        ) : null}
        {inputError ? (
          <p className="kovaak-consent-error" role="alert">
            <span aria-hidden="true">⚠</span>
            <span>{t("kovaak.connection.steamIdInvalidDetail")}</span>
          </p>
        ) : null}
      </div>
    </div>
  );

  /* ── 已连接（点点 0912 拍板）：分数与档位详情不上屏（版权考量），只显示连接状态；
       成绩数据仍保存在本机，Coach 分析时会在后台读取。 ─────────────── */
  const connectedView = (
    <div className="kovaak-module">
      <div className="kovaak-connected">
        <span className="kovaak-connection-status"><strong>{t("kovaak.connection.connectedTitle")}</strong></span>
        <Status tone="success"><span aria-hidden="true">●</span>{t("kovaak.connection.connected")}</Status>
      </div>
      <p className="kovaak-module-note">
        {t("kovaak.connection.lastSync", { time: observedAt(scores?.observed_at ?? null) })}
      </p>
      <div className="kovaak-actions">
        <Button disabled={busy} onClick={() => void refresh()} size="compact" variant="secondary">
          {operation === "refreshing" ? t("kovaak.connection.refreshing") : t("kovaak.connection.refreshScores")}
        </Button>
        <Button disabled={busy} onClick={() => setConfirmRemove(true)} size="compact" variant="ghost">{t("kovaak.connection.stopUsingSource")}</Button>
      </div>
      {!connected ? feedbackMessage : null}
    </div>
  );

  return (
    <div className="kovaak-panel" data-context={context}>
      {connected ? connectedView : connectModule}
      {!connected ? feedbackMessage : null}
      {context === "onboarding" ? (
        <div className="kovaak-onboarding-actions">
          {onSkip ? <Button onClick={onSkip} size="compact" variant="ghost">{t("kovaak.connection.skipStep")}</Button> : null}
          {connected && onContinue ? <Button onClick={onContinue}>{t("kovaak.onboarding.continue")}</Button> : null}
        </div>
      ) : null}

      <Dialog
        footer={<><Button onClick={() => setConfirmRemove(false)} size="compact" variant="secondary">{t("kovaak.connection.cancel")}</Button><Button onClick={() => void remove()} size="compact" variant="danger">{t("kovaak.connection.stopUsing")}</Button></>}
        onClose={() => setConfirmRemove(false)}
        open={confirmRemove}
        title={t("kovaak.connection.stopUsingTitle")}
      >
        <p>{t("kovaak.connection.stopUsingBody")}</p>
      </Dialog>
    </div>
  );
}
