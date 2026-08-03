"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import {
  deleteKovaaKConnection,
  getKovaaKConnection,
  getKovaaKScores,
  refreshKovaaKConnection,
  saveKovaaKConnection,
} from "@/lib/api";
import type { KovaaKScoreItemV1, KovaaKScoresV1 } from "@/lib/types";
import { Badge, Button, Dialog, Field, FieldControl, Notice, Status } from "@/ui/primitives";

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
const CATEGORY_ORDER = ["Control Tracking", "Reactive Tracking", "Flick Tech", "Click Timing"];
const COACH_OPEN_KEY = "aiming-cookie.ui.coach-open";
const COACH_PENDING_INTENT_KEY = "aiming-cookie.ui.coach-pending-intent";

function isSteamProfile(value: string): boolean {
  return STEAM_ID.test(value) || STEAM_PROFILE.test(value);
}

function observedAt(value: string | null): string {
  if (!value) return "暂无成功读取记录";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "已有可用成绩";
  return new Intl.DateTimeFormat("zh-CN", { dateStyle: "medium", timeStyle: "short" }).format(date);
}

function scoreLabel(item: KovaaKScoreItemV1): string {
  return item.completed ? new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 0 }).format(item.score) : "未完成";
}

function categoryLabel(category: string): string {
  return CATEGORY_ORDER.includes(category) ? category : "其它训练项目";
}

function groupItems(items: KovaaKScoreItemV1[]): Array<[string, KovaaKScoreItemV1[]]> {
  const groups = new Map<string, KovaaKScoreItemV1[]>();
  for (const item of items) {
    const label = categoryLabel(item.category);
    groups.set(label, [...(groups.get(label) ?? []), item]);
  }
  return [...groups.entries()].sort(([left], [right]) => {
    const leftIndex = CATEGORY_ORDER.indexOf(left);
    const rightIndex = CATEGORY_ORDER.indexOf(right);
    return (leftIndex < 0 ? CATEGORY_ORDER.length : leftIndex) - (rightIndex < 0 ? CATEGORY_ORDER.length : rightIndex);
  });
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
  const stages = useMemo(() => new Map(scores?.stages.map((stage) => [stage.stage, stage]) ?? []), [scores]);
  const groups = useMemo(() => groupItems(scores?.items ?? []), [scores]);

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
    if (!identityConsent) {
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

  const askCoachAbout = (itemName: string) => {
    const intent = { item_name: itemName };
    try {
      window.sessionStorage.setItem(COACH_PENDING_INTENT_KEY, JSON.stringify(intent));
    } catch {
      // sessionStorage 不可用时仍通过事件把意图带给本页 Coach。
    }
    try {
      window.localStorage.setItem(COACH_OPEN_KEY, "open");
    } catch {
      // 本地偏好写入失败不影响跳转。
    }
    window.dispatchEvent(new CustomEvent("aiming-cookie:coach-kovaak-intent", { detail: intent }));
    window.location.assign("/history");
  };

  const busy = operation !== "idle";
  const feedbackMessage = feedback
    ? feedback.tone === "success"
      ? <Status tone="success">{feedback.message}</Status>
      : <Notice tone={feedback.tone}>{feedback.message}</Notice>
    : null;
  const easierStage = stages.get("easier");
  const mediumStage = stages.get("medium");
  const overallRank = easierStage?.rank_name ?? mediumStage?.rank_name ?? null;
  const remainingByCategory = useMemo(() => {
    const counts = new Map<string, number>();
    for (const item of scores?.items ?? []) {
      if (item.completed) continue;
      const label = categoryLabel(item.category);
      counts.set(label, (counts.get(label) ?? 0) + 1);
    }
    return CATEGORY_ORDER.filter((name) => counts.has(name)).map((name) => [name, counts.get(name) ?? 0] as const);
  }, [scores?.items]);
  const remainingTotal = (scores?.items ?? []).filter((item) => !item.completed).length;

  /* ── 状态 4：正在读取（局部骨架，不阻塞其它操作） ───────────── */
  if (operation === "loading") {
    return (
      <div className="kovaak-panel" data-context={context}>
        <div className="kovaak-module">
          <div className="kovaak-module-read-state">
            <Status tone="neutral"><span className="kovaak-skeleton-dot" />正在读取成绩…</Status>
            <span className="kovaak-module-note">通常只需几秒，可以继续其它操作</span>
          </div>
          <div style={{ marginTop: "10px", width: "62%" }}><div className="kovaak-skeleton" /></div>
          <div style={{ marginTop: "8px", width: "44%" }}><div className="kovaak-skeleton" /></div>
        </div>
      </div>
    );
  }

  /* ── 状态 1–3：未连接 / URL 无效 / 尚未同意 ─────────────────── */
  const connectModule = (
    <div className="kovaak-module">
      <div className="kovaak-connect-form">
        <Field label="Steam 个人资料链接 或 17 位 Steam ID">
          <FieldControl
            data-invalid={inputError ? "true" : undefined}
            onChange={(event) => { setSteamProfile(event.target.value); setInputError(null); }}
            placeholder="粘贴链接或输入数字 ID…"
            value={steamProfile}
          />
        </Field>
        <p className="kovaak-module-note">粘贴完整链接或直接输入数字 ID；不需要登录 Steam，也不会要求授权。</p>
        {inputError ? (
          <p className="kovaak-consent-error" role="alert">
            <span aria-hidden="true">⚠</span>
            <span>没有识别到有效的 Steam 个人资料链接——请检查是否完整粘贴，或直接输入 17 位数字 ID。</span>
          </p>
        ) : null}
        <label className="kovaak-consent">
          <input
            checked={identityConsent}
            onChange={(event) => setIdentityConsent(event.target.checked)}
            type="checkbox"
          />
          <span>我同意使用这个 Steam ID 读取 KovaaK 中的 S2 训练单成绩。该 ID 会保存在本机，方便以后手动刷新；不会在界面中回显，也不会发送给 Coach Provider。</span>
        </label>
        <div className="kovaak-module-actions">
          <Button disabled={!identityConsent || busy} onClick={() => void save()}>
            {operation === "saving" ? "正在读取…" : "读取成绩"}
          </Button>
          {!identityConsent ? <span className="kovaak-module-note">勾选同意后才能读取</span> : null}
        </div>
        <p className="kovaak-module-note">这不是 Aiming Cookie 账号连接——产品没有账号系统。读取后界面不会展示你的 Steam ID。</p>
      </div>
    </div>
  );

  const scoreRows = (items: KovaaKScoreItemV1[]) => (
    <div className="kovaak-score-group">
      {groupItems(items).map(([category, rows]) => {
        const done = rows.filter((item) => item.completed).length;
        return [
          <h3 key={category}>{category}<span>{done} / {rows.length} 完成</span></h3>,
          ...rows.map((item) => (
            <div className="kovaak-score-row" data-completed={item.completed ? "true" : "false"} key={`${item.stage}-${item.name}`}>
              <span className="kovaak-score-name"><strong>{item.name}</strong></span>
              <span className="kovaak-score-value">{item.completed ? scoreLabel(item) : "—"}</span>
              <span className="kovaak-score-tier"><Badge tone="neutral">{item.completed ? item.item_rank_name : "未完成"}</Badge></span>
              <span className="kovaak-score-coach">
                {item.completed ? (
                  <Button onClick={() => askCoachAbout(item.name)} size="compact" variant="ghost">让 Coach 看看</Button>
                ) : null}
              </span>
            </div>
          )),
        ];
      })}
    </div>
  );

  /* ── 状态 7：完全没有成绩 ──────────────────────────────────── */
  const emptyScores = (
    <div className="kovaak-module kovaak-empty">
      <div className="kovaak-empty-title">这个来源还没有可读取的 S2 训练单成绩</div>
      <p>可能是还没有完成训练单项目，或资料未公开。不影响本地分析与 Coach 训练。</p>
      <div style={{ marginTop: "10px" }}>
        <Button disabled={busy} onClick={() => void refresh()} size="compact" variant="ghost">稍后重新读取</Button>
      </div>
    </div>
  );

  /* ── 状态 5/6：成绩可用 / 刷新失败但保留旧成绩 ──────────────── */
  const scoresView = (
    <>
      <div className="kovaak-panel__header">
        <div>
          <h2>{context === "onboarding" ? "已连接 KovaaK 成绩" : "KovaaK 成绩连接"}</h2>
          <p>最近成功同步：{observedAt(scores?.observed_at ?? null)} · 不展示 Steam ID</p>
        </div>
        <div className="kovaak-actions">
          <Button disabled={busy} onClick={() => void refresh()} size="compact" variant="secondary">
            {operation === "refreshing" ? "正在刷新…" : "刷新成绩"}
          </Button>
          <Button disabled={busy} onClick={() => setConfirmRemove(true)} size="compact" variant="ghost">停止使用此来源</Button>
        </div>
      </div>

      {feedbackMessage}

      {!hasScores ? emptyScores : (
        <>
          <div className="kovaak-connected">
            <span className="kovaak-connection-status">
              <strong>S2 训练单</strong>
            </span>
            <span className="kovaak-module-read-state">
              <Status tone="success"><span aria-hidden="true">●</span>成绩可用</Status>
              {overallRank ? <Badge tone="neutral">综合档位：{overallRank}</Badge> : null}
            </span>
          </div>
          <dl className="kovaak-kv">
            <dt>Easier 完成度</dt>
            <dd>{easierStage ? `${easierStage.completed} / ${easierStage.required}` : "0 / 0"}</dd>
            <dt>Medium 完成度</dt>
            <dd>{mediumStage ? `${mediumStage.completed} / ${mediumStage.required}` : "0 / 0"}</dd>
            <dt>Steam ID</dt>
            <dd>保存在本机，不回显</dd>
          </dl>
          <p className="kovaak-disclaimer">成绩只是 Coach 的参考之一，不会替代本地分析结论；不提供排行榜、社交比较或历史曲线。</p>

          <div className="kovaak-scores-layout" id="kovaak-scores-list">
            <div className="kovaak-score-list">
              {(["easier", "medium"] as const).map((stage) => {
                const items = (scores?.items ?? []).filter((item) => item.stage === stage);
                if (!items.length) return null;
                const stageInfo = stages.get(stage);
                return (
                  <section aria-label={`${stage === "easier" ? "Easier" : "Medium"} 项目`} key={stage}>
                    <div className="kovaak-stage-summary">
                      <span>{stage === "easier" ? "Easier" : "Medium"} 完成度 <strong>{stageInfo ? `${stageInfo.completed} / ${stageInfo.required}` : String(items.filter((item) => item.completed).length)}</strong></span>
                      {stageInfo?.rank_name ? <span>档位 <strong>{stageInfo.rank_name}</strong></span> : null}
                    </div>
                    {scoreRows(items)}
                  </section>
                );
              })}
            </div>

            <div className="kovaak-side-cards">
              <div className="kovaak-module">
                <h4>综合档位</h4>
                <div className="kovaak-rank">{overallRank ?? "未评定"}</div>
                <p className="kovaak-kv-note">档位来自 KovaaK 目录规则，只反映成绩进度，不作为能力结论。</p>
              </div>
              <div className="kovaak-module">
                <h4>未完成 {remainingTotal} 项</h4>
                <dl className="kovaak-kv">
                  {remainingByCategory.map(([name, count]) => (
                    <div key={name}><dt>{name}</dt><dd>{count} 项</dd></div>
                  ))}
                </dl>
                <p className="kovaak-kv-note">完成更多项目后完成度与档位会更新；未完成是进度，不是问题。</p>
              </div>
              <div className="kovaak-module">
                <h4>和 Coach 一起用</h4>
                <p className="kovaak-module-note">「让 Coach 看看」只是让 Coach 优先检查——低分本身不能推出阅读、张力、握法、外设或动作问题，结论仍以本地分析为准。</p>
                <div className="kovaak-module-actions">
                  <Button onClick={() => askCoachAbout("S2 训练单成绩")} size="compact" variant="secondary" data-wide="true">让 Coach 看看整体成绩</Button>
                </div>
              </div>
            </div>
          </div>

          <div className="kovaak-module">
            <h4>更换读取来源</h4>
            <p className="kovaak-module-note">换用其它 Steam 个人资料链接或 ID 需要重新同意；旧成绩会保留到下次成功读取为止。</p>
            <div className="kovaak-module-actions">
              <Button onClick={() => { setConnected(false); setIdentityConsent(false); }} size="compact" variant="ghost">重新输入链接 / ID…</Button>
            </div>
          </div>
        </>
      )}
    </>
  );

  return (
    <div className="kovaak-panel" data-context={context}>
      {connected ? scoresView : connectModule}
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
