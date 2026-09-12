"use client";

import { useCallback, useEffect, useState } from "react";

import {
  deleteKovaaKConnection,
  getKovaaKConnection,
  getKovaaKScores,
  refreshKovaaKConnection,
  saveKovaaKConnection,
} from "@/lib/api";
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

function observedAt(value: string | null): string {
  if (!value) return "暂无成功读取记录";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "已有可用成绩";
  return new Intl.DateTimeFormat("zh-CN", { dateStyle: "medium", timeStyle: "short" }).format(date);
}

export function KovaaKConnectionPanel({ context, onContinue, onSkip }: KovaaKConnectionPanelProps) {
  const [connected, setConnected] = useState(false);
  const [scores, setScores] = useState<KovaaKScoresV1 | null>(null);
  const [steamProfile, setSteamProfile] = useState("");
  const [identityConsent, setIdentityConsent] = useState(false);
  const [inputError, setInputError] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<Feedback | null>(null);
  const [operation, setOperation] = useState<Operation>("loading");
  const [confirmRemove, setConfirmRemove] = useState(false);

  const hasScores = scores?.availability === "available";

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
      setFeedback({ tone: "error", message: "KovaaK 连接状态暂时无法读取，请稍后重试。" });
    } else if (scoresResult.status === "rejected") {
      setFeedback({ tone: "error", message: "KovaaK 成绩暂时无法读取，请稍后重试。" });
    } else {
      setFeedback(null);
    }
    setOperation("idle");
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const save = async () => {
    const value = steamProfile.trim();
    if (!isSteamProfile(value)) {
      setInputError("没有识别到有效的 Steam 个人资料链接或 17 位 Steam ID。");
      return;
    }
    // 点点 0912 拍板：settings 去掉同意勾选门槛（愿意输入并点读取即表达意图）；
    // onboarding 向导仍保留勾选。后端合同 identity_consent 恒为 true。
    if (context === "onboarding" && !identityConsent) {
      setInputError("读取前需要同意本次使用该 Steam ID。" );
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
      setFeedback({ tone: "error", message: "连接未能保存，请检查输入后重试。" });
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
      setFeedback({ tone: "success", message: "成绩已更新。" });
    } catch {
      setFeedback({
        tone: hasScores ? "warning" : "error",
        message: hasScores ? "这次没有更新，上次成绩仍然可用。" : "这次没有读到可用成绩，请稍后刷新。",
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
      setFeedback({ tone: "success", message: "KovaaK 连接已移除。" });
    } catch {
      setFeedback({ tone: "error", message: "KovaaK 连接未能移除，请重试。" });
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
            <Status tone="neutral"><span className="kovaak-skeleton-dot" />正在读取成绩…</Status>
            <span className="kovaak-module-note">通常只需几秒，可以继续其它操作</span>
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
        placeholder="粘贴链接或输入数字 ID…"
        value={steamProfile}
      />
      <Button disabled={busy || (context === "onboarding" && !identityConsent)} onClick={() => void save()}>
        {operation === "saving" ? "正在读取…" : "读取成绩"}
      </Button>
    </div>
  );
  const connectModule = (
    <div className="kovaak-module">
      {context === "settings" ? (
        <>
          {/* 0912 线框拍板：卡内自包含标题；同意勾选与说明文字全部退役。 */}
          <h3 className="task6-profile-group-title">KovaaKs 在线成绩</h3>
          <p className="task6-card-desc">粘贴 Steam 链接或 17 位数字 ID；数据只在本机展示。</p>
        </>
      ) : null}
      <div className="kovaak-connect-form">
        {context === "onboarding" ? <Field label="Steam 个人资料链接 或 17 位 Steam ID">{connectRow}</Field> : connectRow}
        {context === "onboarding" ? (
          <>
            <p className="kovaak-module-note">粘贴完整链接或直接输入数字 ID；不需要登录 Steam，也不会要求授权。</p>
            <label className="kovaak-consent">
              <input
                checked={identityConsent}
                onChange={(event) => setIdentityConsent(event.target.checked)}
                type="checkbox"
              />
              <span>我同意使用此 Steam ID 在本机读取 KovaaKs 在线成绩</span>
            </label>
            <p className="kovaak-module-note">
              Steam ID 仅保存在本机、不回显、不发送给 Coach Provider；勾选同意后才能读取成绩。
            </p>
          </>
        ) : null}
        {inputError ? (
          <p className="kovaak-consent-error" role="alert">
            <span aria-hidden="true">⚠</span>
            <span>没有识别到有效的 Steam 个人资料链接——请检查是否完整粘贴，或直接输入 17 位数字 ID。</span>
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
        <span className="kovaak-connection-status"><strong>已连接 KovaaKs 在线成绩</strong></span>
        <Status tone="success"><span aria-hidden="true">●</span>已连接</Status>
      </div>
      <p className="kovaak-module-note">
        最近成功同步：{observedAt(scores?.observed_at ?? null)}。成绩数据只保存在本机，Coach 分析时会在后台读取。
      </p>
      <div className="kovaak-actions">
        <Button disabled={busy} onClick={() => void refresh()} size="compact" variant="secondary">
          {operation === "refreshing" ? "正在刷新…" : "刷新成绩"}
        </Button>
        <Button disabled={busy} onClick={() => setConfirmRemove(true)} size="compact" variant="ghost">停止使用此来源</Button>
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
          {onSkip ? <Button onClick={onSkip} size="compact" variant="ghost">跳过这一步</Button> : null}
          {connected && onContinue ? <Button onClick={onContinue}>继续</Button> : null}
        </div>
      ) : null}

      <Dialog
        footer={<><Button onClick={() => setConfirmRemove(false)} size="compact" variant="secondary">取消</Button><Button onClick={() => void remove()} size="compact" variant="danger">停止使用</Button></>}
        onClose={() => setConfirmRemove(false)}
        open={confirmRemove}
        title="停止使用 KovaaK 成绩来源"
      >
        <p>本地保存的连接与已读取成绩会被移除；不影响本地分析与历史。之后可以重新连接。</p>
      </Dialog>
    </div>
  );
}
