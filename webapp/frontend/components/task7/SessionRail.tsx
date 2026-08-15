"use client";

import { useEffect, useMemo, useRef, useState, type ChangeEvent, type KeyboardEvent } from "react";

export type SessionRailId = string | number;
type OverlayState = "closed" | "opening" | "open" | "closing";

function overlayExitDurationMs(): number {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches ? 120 : 200;
}

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
  onCollapsedChange?: (collapsed: boolean) => void;
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
  onCollapsedChange,
  historyCount = null,
  className,
}: SessionRailProps) {
  const [query, setQuery] = useState("");
  const [narrow, setNarrow] = useState(false);
  const [manuallyCollapsed, setManuallyCollapsed] = useState(false);
  const [overlayState, setOverlayState] = useState<OverlayState>("closed");
  const [overlayMotion, setOverlayMotion] = useState<"animated" | "instant">("animated");
  const railRef = useRef<HTMLElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const toggleButtonRef = useRef<HTMLButtonElement>(null);
  const overlayFrameRef = useRef<number | null>(null);
  const overlayTimerRef = useRef<number | null>(null);
  const overlayVisible = overlayState !== "closed";
  const overlayOpen = overlayState === "opening" || overlayState === "open";

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

  useEffect(() => {
    const media = window.matchMedia("(max-width: 1119px)");
    const sync = () => {
      setNarrow(media.matches);
      if (!media.matches) {
        if (overlayFrameRef.current !== null) window.cancelAnimationFrame(overlayFrameRef.current);
        if (overlayTimerRef.current !== null) window.clearTimeout(overlayTimerRef.current);
        setOverlayState("closed");
      }
    };
    sync();
    media.addEventListener("change", sync);
    return () => media.removeEventListener("change", sync);
  }, []);

  const openOverlay = (instant = false) => {
    if (overlayFrameRef.current !== null) window.cancelAnimationFrame(overlayFrameRef.current);
    if (overlayTimerRef.current !== null) window.clearTimeout(overlayTimerRef.current);
    setOverlayMotion(instant ? "instant" : "animated");
    if (instant) {
      setOverlayState("open");
      overlayFrameRef.current = window.requestAnimationFrame(() => setOverlayMotion("animated"));
      return;
    }
    setOverlayState("opening");
    overlayFrameRef.current = window.requestAnimationFrame(() => {
      overlayFrameRef.current = window.requestAnimationFrame(() => setOverlayState("open"));
    });
  };

  const closeOverlay = (instant = false) => {
    if (overlayFrameRef.current !== null) window.cancelAnimationFrame(overlayFrameRef.current);
    if (overlayTimerRef.current !== null) window.clearTimeout(overlayTimerRef.current);
    setOverlayMotion(instant ? "instant" : "animated");
    if (instant) {
      setOverlayState("closed");
      overlayFrameRef.current = window.requestAnimationFrame(() => {
        setOverlayMotion("animated");
        toggleButtonRef.current?.focus();
      });
      return;
    }
    setOverlayState("closing");
    overlayTimerRef.current = window.setTimeout(() => {
      setOverlayState("closed");
      window.requestAnimationFrame(() => toggleButtonRef.current?.focus());
    }, overlayExitDurationMs());
  };

  const toggleRail = (instant = false) => {
    if (narrow) {
      if (overlayOpen) closeOverlay(instant);
      else openOverlay(instant);
      return;
    }
    setManuallyCollapsed((collapsed) => {
      const next = !collapsed;
      onCollapsedChange?.(next);
      return next;
    });
  };

  const openRail = (focusSearch = false, instant = false) => {
    if (narrow) openOverlay(instant);
    else if (manuallyCollapsed) {
      setManuallyCollapsed(false);
      onCollapsedChange?.(false);
    }
    if (focusSearch) window.requestAnimationFrame(() => searchRef.current?.focus());
  };

  useEffect(() => {
    const onKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.ctrlKey && event.key === "\\") {
        event.preventDefault();
        toggleRail(true);
      }
      if (event.key === "Escape" && overlayOpen) closeOverlay(true);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [narrow, overlayOpen]);

  useEffect(() => () => {
    if (overlayFrameRef.current !== null) window.cancelAnimationFrame(overlayFrameRef.current);
    if (overlayTimerRef.current !== null) window.clearTimeout(overlayTimerRef.current);
  }, []);

  useEffect(() => {
    if (overlayOpen) window.requestAnimationFrame(() => toggleButtonRef.current?.focus());
  }, [overlayOpen]);

  const trapOverlayFocus = (event: KeyboardEvent<HTMLElement>) => {
    if (!overlayOpen || event.key !== "Tab") return;
    const focusable = railRef.current?.querySelectorAll<HTMLElement>("button:not(:disabled), input:not(:disabled), summary, [href]");
    if (!focusable?.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };

  const railClassName = ["task7-session-rail", className].filter(Boolean).join(" ");
  const collapsed = narrow || manuallyCollapsed;
  return (
    <>
      {narrow && overlayVisible ? <button aria-label="关闭会话栏" className="task7-session-rail__scrim" data-motion={overlayMotion} data-state={overlayState} onClick={() => closeOverlay()} type="button" /> : null}
      <aside
        aria-label="会话"
        aria-hidden={overlayState === "closing" || undefined}
        aria-modal={overlayOpen || undefined}
        className={railClassName}
        data-collapsed={collapsed || undefined}
        data-motion={overlayMotion}
        data-overlay={overlayVisible || undefined}
        data-overlay-state={overlayVisible ? overlayState : undefined}
        inert={overlayState === "closing" || undefined}
        onKeyDown={trapOverlayFocus}
        ref={railRef}
        role={overlayOpen ? "dialog" : undefined}
      >
        {collapsed && !overlayVisible ? (
          <div className="task7-session-rail__iconbar" aria-label="会话栏快捷操作">
            <button aria-label="展开会话栏" className="task7-session-rail__icon-button" onClick={() => openRail()} ref={toggleButtonRef} title="收起/展开会话栏" type="button">→</button>
            <button aria-label="新建对话" className="task7-session-rail__icon-button" onClick={onNewSession} title="新建对话" type="button">+</button>
            <button aria-label="搜索会话" className="task7-session-rail__icon-button" onClick={() => openRail(true)} title="搜索会话" type="button">⌕</button>
            <button aria-label="训练历史" className="task7-session-rail__icon-button" onClick={onHistory} title="训练历史" type="button">◔</button>
            <button aria-label="系统设置" className="task7-session-rail__icon-button" onClick={onSettings} title="系统设置" type="button">⚙</button>
          </div>
        ) : <>
          <div className="task7-session-rail__header">
            <div className="task7-session-rail__header-actions">
              <button className="task7-session-rail__new" onClick={onNewSession} type="button">
                <span aria-hidden="true" className="task7-session-rail__new-icon">+</span>
                <span>新建对话</span>
              </button>
              <button aria-label="收起/展开会话栏" className="task7-session-rail__collapse" onClick={() => toggleRail()} ref={toggleButtonRef} title="收起/展开会话栏" type="button">←</button>
            </div>
          </div>

          <label className="task7-session-rail__search">
            <span aria-hidden="true" className="task7-session-rail__search-icon">⌕</span>
            <span className="task7-session-rail__sr-only">搜索会话</span>
            <input onChange={handleSearch} placeholder="搜索会话" ref={searchRef} type="search" value={query} />
            {query ? <button aria-label="清除搜索" className="task7-session-rail__search-clear" onClick={() => { setQuery(""); onSearchChange?.(""); }} type="button">×</button> : null}
          </label>

          <nav aria-label="会话列表" className="task7-session-rail__list">
        {visible.length ? visible.map((session) => {
          const title = sessionTitle(session);
          const date = sessionDate(session);
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
                {session.summary && session.summary !== title ? <span className="task7-session-rail__session-summary">{session.summary}</span> : null}
                {!session.summary && (session.lastMessagePreview || session.last_message_preview) ? <span className="task7-session-rail__session-summary">{session.lastMessagePreview || session.last_message_preview}</span> : null}
                {date ? <time className="task7-session-rail__session-date" dateTime={session.updatedAt || session.updated_at || session.createdAt || session.created_at || undefined}>{date}</time> : null}
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
            <button aria-label="训练历史" className="task7-session-rail__footer-row" onClick={onHistory} type="button"><span className="task7-session-rail__footer-label"><span aria-hidden="true">◔</span><span>训练历史</span></span>{historyCount === null ? null : <span className="task7-session-rail__footer-count">{historyCount}</span>}</button>
            <button aria-label="系统设置" className="task7-session-rail__footer-row" onClick={onSettings} type="button"><span className="task7-session-rail__footer-label"><span aria-hidden="true">⚙</span><span>系统设置</span></span></button>
          </footer>
        </>}
      </aside>
    </>
  );
}

export default SessionRail;
