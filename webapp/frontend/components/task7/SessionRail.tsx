"use client";

import { useEffect, useMemo, useRef, useState, type ChangeEvent, type KeyboardEvent } from "react";

import { IconClose, IconHistory, IconPlus, IconSearch, IconSettings } from "@/ui/icons";

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
  const searchRef = useRef<HTMLInputElement>(null);
  const railRef = useRef<HTMLElement>(null);

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
      .sort((a, b) => sessionTimestamp(b) - sessionTimestamp(a));
  }, [query, sessions]);

  const handleSearch = (event: ChangeEvent<HTMLInputElement>) => {
    const nextQuery = event.target.value;
    setQuery(nextQuery);
    onSearchChange?.(nextQuery);
  };

  const railClassName = ["task7-session-rail", className].filter(Boolean).join(" ");
  return (
    <aside aria-label="会话" className={railClassName} ref={railRef}>
      <div className="task7-session-rail__header">
        <div className="task7-session-rail__header-actions">
          <button className="task7-session-rail__new" onClick={onNewSession} type="button">
            <IconPlus />
            <span>新建对话</span>
          </button>
        </div>
      </div>

      <label className="task7-session-rail__search">
        <span aria-hidden="true" className="task7-session-rail__search-icon"><IconSearch /></span>
        <span className="task7-session-rail__sr-only">搜索会话</span>
        <input onChange={handleSearch} placeholder="搜索会话" ref={searchRef} type="search" value={query} />
        {query ? <button aria-label="清除搜索" className="task7-session-rail__search-clear" onClick={() => { setQuery(""); onSearchChange?.(""); }} type="button"><IconClose /></button> : null}
      </label>

      <nav aria-label="会话列表" className="task7-session-rail__list">
    {visible.length ? visible.map((session) => {
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
              {onArchiveSession ? <button aria-label={`归档 ${title}`} className="task7-session-rail__item-action" onClick={(event) => { event.stopPropagation(); onArchiveSession(session); }} type="button">归档</button> : null}
              {onSoftDeleteSession ? <button aria-label={`删除 ${title}`} className="task7-session-rail__item-action task7-session-rail__item-action--danger" onClick={(event) => { event.stopPropagation(); onSoftDeleteSession(session); }} type="button">删除</button> : null}
            </span>
          ) : null}
        </div>
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
