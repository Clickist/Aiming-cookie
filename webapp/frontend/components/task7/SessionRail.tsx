"use client";

import { useEffect, useMemo, useRef, useState, type ChangeEvent, type KeyboardEvent } from "react";

import { IconArchive, IconCheck, IconClose, IconHistory, IconPlus, IconSearch, IconSettings, IconTrash } from "@/ui/icons";
import { Button } from "@/ui/primitives";
import { startWindowDraggingOnBackground } from "@/components/task3/TauriWindowControls";

export type SessionRailId = string | number;

export interface SessionRailSession {
  id: SessionRailId;
  kind?: string | null;
  title?: string | null;
  label?: string | null;
  name?: string | null;
  summary?: string | null;
  lastMessage?: string | null;
  lastMessagePreview?: string | null;
  last_message_preview?: string | null;
  scenario?: string | null;
  scenarioName?: string | null;
  updatedAt?: string | null;
  createdAt?: string | null;
  updated_at?: string | null;
  created_at?: string | null;
  status?: string | null;
  deletedAt?: string | null;
  deleted_at?: string | null;
  archived?: boolean;
  isArchived?: boolean;
}

export interface SessionRailProps {
  sessions: readonly SessionRailSession[];
  currentSessionId?: SessionRailId | null;
  onNewSession?: () => void;
  onSelectSession?: (session: SessionRailSession) => void;
  onHistory?: () => void;
  onSettings?: () => void;
  onSearchChange?: (query: string) => void;
  onArchiveSession?: (session: SessionRailSession) => void;
  onSoftDeleteSession?: (session: SessionRailSession) => void;
  providerStatus?: "ready" | "waiting" | "unavailable" | "loading";
  historyCount?: number | null;
  className?: string;
}

function sessionTitle(session: SessionRailSession): string {
  const title = session.title?.trim();
  if (title && title !== "新对话") return title;
  const preview = session.lastMessagePreview?.trim() || session.last_message_preview?.trim();
  if (title === "新对话" && !preview) return "新对话"; // 草稿态：还没有消息，保持"新对话"
  return session.label?.trim() || session.name?.trim() || session.summary?.trim()
    || preview || "未命名对话";
}

function isArchived(session: SessionRailSession): boolean {
  return session.archived ?? session.isArchived ?? (session.status === "archived" || session.status === "deleted" || Boolean(session.deletedAt || session.deleted_at));
}

function sessionTimestamp(session: SessionRailSession): number {
  const value = session.updatedAt || session.updated_at || session.createdAt || session.created_at;
  if (!value) return 0;
  return new Date(value).getTime() || 0;
}

// 无时间戳的会话（如草稿）视为最新，排在所在分组首位，而不是沉底到"更早"。
function sessionOrderStamp(session: SessionRailSession): number {
  return sessionTimestamp(session) || Number.MAX_SAFE_INTEGER;
}

const SESSION_GROUP_DEFS = [
  { key: "today", label: "今天" },
  { key: "yesterday", label: "昨天" },
  { key: "week", label: "近 7 天" },
  { key: "older", label: "更早" },
] as const;

// 组内默认只渲染前 N 条（0827 拍板）：超出的部分收进组尾「显示全部」开关，
// 点开全量后再点一次回到预览条数。
const SESSION_GROUP_PREVIEW_COUNT = 5;

type SessionGroupKey = (typeof SESSION_GROUP_DEFS)[number]["key"];

// 按本地自然日边界分桶；缺时间戳归入"今天"。用日历日而非固定 86400 秒，避免夏令时偏移。
function localDayStart(offsetDays: number): number {
  const now = new Date();
  return new Date(now.getFullYear(), now.getMonth(), now.getDate() - offsetDays).getTime();
}

function sessionGroupKey(timestamp: number, dayBounds: readonly [number, number, number]): SessionGroupKey {
  if (!timestamp || timestamp >= dayBounds[0]) return "today";
  if (timestamp >= dayBounds[1]) return "yesterday";
  if (timestamp >= dayBounds[2]) return "week";
  return "older";
}

function sessionDate(session: SessionRailSession): string | null {
  const value = session.updatedAt || session.updated_at || session.createdAt || session.created_at;
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", { month: "numeric", day: "numeric" }).format(date);
}

export function SessionRail({
  sessions,
  currentSessionId = null,
  onNewSession,
  onSelectSession,
  onHistory,
  onSettings,
  onSearchChange,
  onArchiveSession,
  onSoftDeleteSession,
  historyCount = null,
  className,
}: SessionRailProps) {
  const [query, setQuery] = useState("");
  const [pendingDeleteId, setPendingDeleteId] = useState<SessionRailId | null>(null);
  // 每组一个独立开关，用一个 record 统一管理：缺省（false）＝只显示前
  // SESSION_GROUP_PREVIEW_COUNT 条；纯 UI 状态，不参与数据获取。
  const [expandedGroups, setExpandedGroups] = useState<Record<string, boolean>>({});
  // 列表两端渐隐（0910 拍板对齐参考图）：仅在对应一端还有未滚动到的内容时
  // 才淡出，滚到边即恢复实色——静态遮罩会一直削首尾条目，故滚动感知。
  const [edgeFade, setEdgeFade] = useState({ top: false, bottom: false });
  const searchRef = useRef<HTMLInputElement>(null);
  const railRef = useRef<HTMLElement>(null);
  const listRef = useRef<HTMLElement>(null);

  const syncEdgeFade = useRef(() => {
    const el = listRef.current;
    if (!el) return;
    const next = {
      top: el.scrollTop > 1,
      bottom: el.scrollTop + el.clientHeight < el.scrollHeight - 1,
    };
    setEdgeFade((prev) => (prev.top === next.top && prev.bottom === next.bottom ? prev : next));
  }).current;

  const visible = useMemo(() => {
    const normalizedQuery = query.trim().toLocaleLowerCase();
    return sessions
      .filter((session) => {
        if (isArchived(session)) return false;
        if (!normalizedQuery) return true;
        return [sessionTitle(session), session.summary, session.lastMessage, session.lastMessagePreview, session.last_message_preview]
          .filter(Boolean)
          .some((value) => value!.toLocaleLowerCase().includes(normalizedQuery));
      })
      .sort((a, b) => sessionOrderStamp(b) - sessionOrderStamp(a));
  }, [query, sessions]);

  // 内容（会话集/搜索过滤/组展开）或窗口尺寸变化后重算渐隐端。
  useEffect(() => {
    syncEdgeFade();
    window.addEventListener("resize", syncEdgeFade);
    return () => window.removeEventListener("resize", syncEdgeFade);
  }, [visible, expandedGroups, syncEdgeFade]);

  // 时间分组（今天/昨天/近 7 天/更早），只保留非空组；组内沿用 visible 的倒序。
  const groups = useMemo(() => {
    const dayBounds: readonly [number, number, number] = [localDayStart(0), localDayStart(1), localDayStart(7)];
    const bucketed = new Map<SessionGroupKey, SessionRailSession[]>(SESSION_GROUP_DEFS.map(({ key }) => [key, []]));
    for (const session of visible) {
      bucketed.get(sessionGroupKey(sessionTimestamp(session), dayBounds))!.push(session);
    }
    return SESSION_GROUP_DEFS
      .map(({ key, label }) => ({ key, label, items: bucketed.get(key)! }))
      .filter((group) => group.items.length > 0);
  }, [visible]);

  const handleSearch = (event: ChangeEvent<HTMLInputElement>) => {
    const nextQuery = event.target.value;
    setQuery(nextQuery);
    onSearchChange?.(nextQuery);
  };

  const railClassName = ["task7-session-rail", className].filter(Boolean).join(" ");
  return (
    <aside aria-label="会话" className={railClassName} ref={railRef}>
      {/* v6（0910 拍板）：横跨顶栏拆除后 logo 归左栏；与下方一体、无分界线。
          品牌行兼作窗口拖拽区（左键空白处），补上被拆掉的顶栏拖拽。 */}
      <div className="task7-session-rail__brand" onMouseDown={startWindowDraggingOnBackground}>
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img className="task7-session-rail__brand-mark" src="/logo-mark.png" alt="" />
        <span className="task7-session-rail__brand-name">Aiming&nbsp;Cookie</span>
      </div>
      <div className="task7-session-rail__header">
        <div className="task7-session-rail__header-actions">
          {/* primary 视觉走共享 Button 原语（digests §9：不再手抄 primary 填充与 hover 公式），
              本类只保留 rail 内的布局伸缩。 */}
          <Button className="task7-session-rail__new" onClick={onNewSession} variant="primary">
            <IconPlus />
            <span>新建对话</span>
          </Button>
        </div>
      </div>

      <label className="task7-session-rail__search">
        <span aria-hidden="true" className="task7-session-rail__search-icon"><IconSearch /></span>
        <span className="task7-session-rail__sr-only">搜索会话</span>
        <input onChange={handleSearch} placeholder="搜索会话" ref={searchRef} type="search" value={query} />
        {query ? <button aria-label="清除搜索" className="task7-session-rail__search-clear" onClick={() => { setQuery(""); onSearchChange?.(""); }} type="button"><IconClose /></button> : null}
      </label>

      <nav
        aria-label="会话列表"
        className="task7-session-rail__list"
        data-fade-bottom={edgeFade.bottom || undefined}
        data-fade-top={edgeFade.top || undefined}
        onScroll={syncEdgeFade}
        ref={listRef}
      >
    {groups.length ? groups.map((group) => {
      // 默认只渲染前 5 条，超出走组尾「显示全部」开关（0827 拍板）；
      // 数量角标始终展示该组总数，不受预览截断影响。
      const expanded = expandedGroups[group.key] ?? false;
      const shownItems = expanded ? group.items : group.items.slice(0, SESSION_GROUP_PREVIEW_COUNT);
      return (
      <section className="task7-session-rail__group" key={group.key}>
        <div className="task7-session-rail__group-summary">
          <span className="task7-session-rail__group-label">{group.label}</span>
          <span className="task7-session-rail__count">{group.items.length}</span>
        </div>
        <div className="task7-session-rail__group-items">
    {shownItems.map((session) => {
      const title = sessionTitle(session);
      const date = sessionDate(session);
      const summaryLine = session.summary && session.summary !== title
        ? session.summary
        : (!session.summary && (session.lastMessagePreview || session.last_message_preview))
          ? (session.lastMessagePreview || session.last_message_preview)
          : "";
      const current = currentSessionId !== null && String(currentSessionId) === String(session.id);
      return (
        <div className="task7-session-rail__item" data-current={current || undefined} key={String(session.id)} role="listitem">
          <button
            aria-current={current ? "page" : undefined}
            className="task7-session-rail__session"
            onClick={() => onSelectSession?.(session)}
            type="button"
          >
            <span className="task7-session-rail__session-title">{title}</span>
            <span aria-hidden={!summaryLine ? true : undefined} className="task7-session-rail__session-summary">{summaryLine}</span>
            <time className="task7-session-rail__session-date" dateTime={session.updatedAt || session.updated_at || session.createdAt || session.created_at || undefined}>{date ?? ""}</time>
          </button>
          {session.id !== "draft" && (onArchiveSession || onSoftDeleteSession) ? (
            <span className="task7-session-rail__item-actions">
              {onArchiveSession ? <button aria-label={`归档 ${title}`} className="task7-session-rail__item-action" onClick={(event) => { event.stopPropagation(); onArchiveSession(session); }} title="归档" type="button"><IconArchive /></button> : null}
              {onSoftDeleteSession ? (
                pendingDeleteId === session.id ? (
                  <button aria-label={`确认删除 ${title}`} className="task7-session-rail__item-action task7-session-rail__item-action--danger task7-session-rail__item-action--confirm" onClick={(event) => { event.stopPropagation(); setPendingDeleteId(null); onSoftDeleteSession(session); }} title="再次点击确认删除" type="button"><IconCheck /></button>
                ) : (
                  <button aria-label={`删除 ${title}`} className="task7-session-rail__item-action task7-session-rail__item-action--danger" onClick={(event) => { event.stopPropagation(); setPendingDeleteId(session.id); }} title="删除" type="button"><IconTrash /></button>
                )
              ) : null}
            </span>
          ) : null}
        </div>
      );
    })}
    {group.items.length > SESSION_GROUP_PREVIEW_COUNT ? (
      <button
        aria-expanded={expanded}
        className="task7-session-rail__group-toggle"
        onClick={() => setExpandedGroups((current) => ({ ...current, [group.key]: !expanded }))}
        type="button"
      >
        {expanded ? `显示前 ${SESSION_GROUP_PREVIEW_COUNT} 条` : `显示全部 ${group.items.length} 条`}
      </button>
    ) : null}
        </div>
      </section>
      );
    }) : <p className="task7-session-rail__empty">{query ? "没有匹配的会话" : "还没有会话"}</p>}
      </nav>
      <footer className="task7-session-rail__footer">
        <button aria-label="训练历史" className="task7-session-rail__footer-row" onClick={onHistory} type="button"><span className="task7-session-rail__footer-label"><IconHistory /><span>训练历史</span></span>{historyCount === null ? null : <span className="task7-session-rail__footer-count">{historyCount}</span>}</button>
        <button aria-label="系统设置" className="task7-session-rail__footer-row" onClick={onSettings} type="button"><span className="task7-session-rail__footer-label"><IconSettings /><span>系统设置</span></span></button>
      </footer>
    </aside>
  );
}

export default SessionRail;
