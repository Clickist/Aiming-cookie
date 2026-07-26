"use client";

import {
  useEffect,
  useId,
  type AnchorHTMLAttributes,
  type ButtonHTMLAttributes,
  type HTMLAttributes,
  type InputHTMLAttributes,
  type ReactNode,
} from "react";

type Tone = "neutral" | "info" | "success" | "warning" | "error";

type ButtonCommonProps = {
  variant?: "primary" | "secondary" | "ghost" | "danger";
  size?: "default" | "compact";
  className?: string;
  children?: ReactNode;
};

export type ButtonProps = ButtonCommonProps &
  (
    | (ButtonHTMLAttributes<HTMLButtonElement> & { href?: never })
    | (AnchorHTMLAttributes<HTMLAnchorElement> & { href: string })
  );

export function Button({ variant = "primary", size = "default", className, children, ...props }: ButtonProps) {
  const classes = ["ac-button", className].filter(Boolean).join(" ");
  if ("href" in props && props.href) {
    return (
      <a {...props} className={classes} data-size={size === "compact" ? "compact" : undefined} data-variant={variant}>
        {children}
      </a>
    );
  }
  const buttonProps = props as ButtonHTMLAttributes<HTMLButtonElement>;
  return (
    <button
      {...buttonProps}
      className={classes}
      data-size={size === "compact" ? "compact" : undefined}
      data-variant={variant}
      type={buttonProps.type ?? "button"}
    >
      {children}
    </button>
  );
}

export interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  label: string;
  size?: "default" | "compact";
}

export function IconButton({ label, size = "default", className, children, type = "button", ...props }: IconButtonProps) {
  return (
    <button
      {...props}
      aria-label={label}
      className={["ac-icon-button", className].filter(Boolean).join(" ")}
      data-size={size === "compact" ? "compact" : undefined}
      type={type}
    >
      {children}
    </button>
  );
}

export interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  tone?: Tone;
}

export function Badge({ tone = "neutral", className, children, ...props }: BadgeProps) {
  return (
    <span {...props} className={["ac-badge", className].filter(Boolean).join(" ")} data-tone={tone}>
      {children}
    </span>
  );
}

export function Status({ tone = "neutral", className, children, ...props }: BadgeProps) {
  return (
    <span {...props} className={["ac-status", className].filter(Boolean).join(" ")} data-tone={tone} role={props.role ?? "status"}>
      {children}
    </span>
  );
}

export interface NoticeProps extends Omit<HTMLAttributes<HTMLDivElement>, "title"> {
  tone?: Exclude<Tone, "neutral" | "success">;
  title?: ReactNode;
}

export function Notice({ tone = "info", title, className, children, ...props }: NoticeProps) {
  return (
    <div {...props} className={["ac-notice", className].filter(Boolean).join(" ")} data-tone={tone} role="note">
      {title ? <div className="ac-notice__title">{title}</div> : null}
      <div className="ac-notice__body">{children}</div>
    </div>
  );
}

export interface FieldProps extends HTMLAttributes<HTMLDivElement> {
  label: ReactNode;
  hint?: ReactNode;
  error?: ReactNode;
  children: ReactNode;
}

export function Field({ label, hint, error, className, children, ...props }: FieldProps) {
  return (
    <div {...props} className={["ac-field", className].filter(Boolean).join(" ")}>
      <div className="ac-field__label">{label}</div>
      {children}
      {error ? <div className="ac-field__error" role="alert">{error}</div> : hint ? <div className="ac-field__hint">{hint}</div> : null}
    </div>
  );
}

export function FieldControl(props: InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={["ac-field__control", props.className].filter(Boolean).join(" ")} />;
}

export interface PanelProps extends Omit<HTMLAttributes<HTMLElement>, "title"> {
  tone?: "default" | "recessed";
  title?: ReactNode;
}

export function Panel({ tone = "default", title, className, children, ...props }: PanelProps) {
  return (
    <section {...props} className={["ac-panel", className].filter(Boolean).join(" ")} data-tone={tone}>
      {title ? <div className="ac-panel__header">{title}</div> : null}
      <div className="ac-panel__body">{children}</div>
    </section>
  );
}

export const Surface = Panel;

export interface TabItem {
  value: string;
  label: ReactNode;
  disabled?: boolean;
}

export interface TabsProps extends HTMLAttributes<HTMLDivElement> {
  items: readonly TabItem[];
  value: string;
  onValueChange: (value: string) => void;
}

export function Tabs({ items, value, onValueChange, className, ...props }: TabsProps) {
  return (
    <div {...props} className={["ac-tabs", className].filter(Boolean).join(" ")} role="tablist">
      {items.map((item) => (
        <button
          aria-selected={item.value === value}
          className="ac-tab"
          disabled={item.disabled}
          key={item.value}
          onClick={() => onValueChange(item.value)}
          role="tab"
          type="button"
        >
          {item.label}
        </button>
      ))}
    </div>
  );
}

function useModalInteraction(open: boolean, onClose: () => void, containerId: string) {
  useEffect(() => {
    if (!open) return undefined;
    const previous = document.activeElement as HTMLElement | null;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
        return;
      }
      if (event.key !== "Tab") return;
      const container = document.getElementById(containerId);
      if (!container) return;
      const focusable = Array.from(
        container.querySelectorAll<HTMLElement>(
          "button:not([disabled]),a[href],input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex='-1'])",
        ),
      );
      if (focusable.length === 0) return;
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
    document.addEventListener("keydown", onKeyDown);
    const firstFocusable = document.querySelector<HTMLElement>(`#${containerId} button:not([disabled]), #${containerId} [href], #${containerId} input:not([disabled])`);
    firstFocusable?.focus();
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      previous?.focus();
    };
  }, [containerId, onClose, open]);
}

export interface DrawerProps {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  children: ReactNode;
  side?: "left" | "right";
}

export function Drawer({ open, onClose, title, children, side = "right" }: DrawerProps) {
  const id = useId().replaceAll(":", "");
  const titleId = `${id}-title`;
  useModalInteraction(open, onClose, id);
  if (!open) return null;
  return (
    <>
      <div className="ac-drawer-backdrop" onMouseDown={onClose} />
      <aside aria-labelledby={titleId} aria-modal="true" className="ac-drawer" data-side={side} id={id} role="dialog">
        <header className="ac-drawer__header">
          <h2 id={titleId}>{title}</h2>
          <IconButton label="Close" onClick={onClose} size="compact">×</IconButton>
        </header>
        <div className="ac-drawer__body">{children}</div>
      </aside>
    </>
  );
}

export const Sheet = Drawer;

export interface ToastProps extends HTMLAttributes<HTMLDivElement> {
  tone?: Tone;
  live?: "polite" | "assertive";
}

export function Toast({ tone = "neutral", live = "polite", className, children, ...props }: ToastProps) {
  return (
    <div {...props} aria-live={live} className={["ac-toast", className].filter(Boolean).join(" ")} data-tone={tone} role={live === "assertive" ? "alert" : "status"}>
      {children}
    </div>
  );
}

export function Loading({ children = "Loading…", className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div {...props} aria-live="polite" className={["ac-state", className].filter(Boolean).join(" ")} role="status"><div className="ac-state__title">{children}</div></div>;
}

export function Empty({ title = "Nothing here yet.", children, className, ...props }: HTMLAttributes<HTMLDivElement> & { title?: ReactNode }) {
  return <div {...props} className={["ac-state", className].filter(Boolean).join(" ")}><div className="ac-state__title">{title}</div>{children ? <div>{children}</div> : null}</div>;
}

export function ErrorState({ title = "Something went wrong.", children, className, ...props }: HTMLAttributes<HTMLDivElement> & { title?: ReactNode }) {
  return <div {...props} aria-live="assertive" className={["ac-state", className].filter(Boolean).join(" ")} data-tone="error" role="alert"><div className="ac-state__title">{title}</div>{children ? <div>{children}</div> : null}</div>;
}

export interface DialogProps {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
}

export function Dialog({ open, onClose, title, children, footer }: DialogProps) {
  const id = useId().replaceAll(":", "");
  const titleId = `${id}-title`;
  useModalInteraction(open, onClose, id);
  if (!open) return null;
  return (
    <>
      <div className="ac-dialog-backdrop" onMouseDown={onClose} />
      <section aria-labelledby={titleId} aria-modal="true" className="ac-dialog" id={id} role="dialog">
        <header className="ac-dialog__header">
          <h2 id={titleId}>{title}</h2>
          <button aria-label="Close" className="ac-dialog__close" onClick={onClose} type="button">×</button>
        </header>
        <div className="ac-dialog__body">{children}</div>
        {footer ? <footer className="ac-dialog__footer">{footer}</footer> : null}
      </section>
    </>
  );
}
