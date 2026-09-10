import type { SVGProps } from "react";

type IconProps = SVGProps<SVGSVGElement>;

function Icon({ children, ...props }: IconProps) {
  return (
    <svg aria-hidden="true" className="ac-icon" fill="none" height="16" viewBox="0 0 16 16" width="16" {...props}>
      {children}
    </svg>
  );
}

const stroke = { stroke: "currentColor", strokeLinecap: "round" as const, strokeLinejoin: "round" as const, strokeWidth: 1.5 };

export function IconPlus(props: IconProps) {
  return <Icon {...props}><path d="M8 3.5v9M3.5 8h9" {...stroke} /></Icon>;
}

export function IconSearch(props: IconProps) {
  return <Icon {...props}><circle cx="7" cy="7" r="3.5" {...stroke} /><path d="m12.5 12.5-2.2-2.2" {...stroke} /></Icon>;
}

export function IconChevronLeft(props: IconProps) {
  return <Icon {...props}><path d="M10 3.5 5.5 8 10 12.5" {...stroke} /></Icon>;
}

export function IconChevronRight(props: IconProps) {
  return <Icon {...props}><path d="M6 3.5 10.5 8 6 12.5" {...stroke} /></Icon>;
}

export function IconChevronDown(props: IconProps) {
  return <Icon {...props}><path d="M3.5 6 8 10.5 12.5 6" {...stroke} /></Icon>;
}

export function IconHistory(props: IconProps) {
  return (
    <Icon {...props}>
      <circle cx="8" cy="8.5" r="5" {...stroke} />
      <path d="M8 6v3l2 1.2" {...stroke} />
      <path d="M5.5 3.2 4 4.5" {...stroke} />
    </Icon>
  );
}

export function IconSettings(props: IconProps) {
  return (
    <Icon {...props}>
      <circle cx="8" cy="8" r="2.2" {...stroke} />
      <path d="M8 2.8v1.3M8 11.9v1.3M2.8 8h1.3M11.9 8h1.3M4.3 4.3l.9.9M10.8 10.8l.9.9M11.7 4.3l-.9.9M5.2 10.8l-.9.9" {...stroke} />
    </Icon>
  );
}

export function IconClose(props: IconProps) {
  return <Icon {...props}><path d="m4 4 8 8M12 4 4 12" {...stroke} /></Icon>;
}

export function IconSend(props: IconProps) {
  return <Icon {...props}><path d="M8 12.5V3.5M4.5 7 8 3.5 11.5 7" {...stroke} /></Icon>;
}

export function IconStop(props: IconProps) {
  return <Icon {...props}><rect height="7" rx="1" width="7" x="4.5" y="4.5" {...stroke} /></Icon>;
}

export function IconCheck(props: IconProps) {
  return <Icon {...props}><path d="m3.5 8.2 2.8 2.8 6.2-6.5" {...stroke} /></Icon>;
}

/* 工作态语义图标（0828：思考/工具步骤行首的 glyph，替代原圆点时间线）。 */

export function IconSpark(props: IconProps) {
  return <Icon {...props}><path d="M8 1.8c.55 3.4 2.1 4.95 5.5 5.5-3.4.55-4.95 2.1-5.5 5.5-.55-3.4-2.1-4.95-5.5-5.5 3.4-.55 4.95-2.1 5.5-5.5Z" fill="currentColor" /></Icon>;
}

export function IconFileText(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M9.5 2H4.75v12h6.5V4L9.5 2Z" {...stroke} />
      <path d="M9.5 2v2h2" {...stroke} />
      <path d="M6.4 8h3.2M6.4 10.4h3.2" {...stroke} />
    </Icon>
  );
}

export function IconFolder(props: IconProps) {
  return <Icon {...props}><path d="M2.5 4.2h4l1.3 1.6h5.7v7H2.5v-8.6Z" {...stroke} /></Icon>;
}

export function IconArchive(props: IconProps) {
  return (
    <Icon {...props}>
      <rect height="2.4" rx="0.6" width="11" x="2.5" y="2.8" {...stroke} />
      <path d="M3.8 5.2v6.4a1.4 1.4 0 0 0 1.4 1.4h5.6a1.4 1.4 0 0 0 1.4-1.4V5.2" {...stroke} />
      <path d="M6.4 8.2h3.2" {...stroke} />
    </Icon>
  );
}

export function IconTrash(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M2.5 4h11" {...stroke} />
      <path d="M5.5 4V2.9a.9.9 0 0 1 .9-.9h3.2a.9.9 0 0 1 .9.9V4" {...stroke} />
      <path d="m4 4 .7 8.6a1.4 1.4 0 0 0 1.4 1.3h3.8a1.4 1.4 0 0 0 1.4-1.3L12 4" {...stroke} />
      <path d="M6.7 7v3.8M9.3 7v3.8" {...stroke} />
    </Icon>
  );
}

export function IconChart(props: IconProps) {
  return <Icon {...props}><path d="M2.5 2.5v11h11" {...stroke} /><path d="m4.8 9.8 2.4-3 2 1.6 3.1-4" {...stroke} /></Icon>;
}

export function IconDatabase(props: IconProps) {
  return (
    <Icon {...props}>
      <ellipse cx="8" cy="3.8" rx="5" ry="1.9" {...stroke} />
      <path d="M3 3.8v8.4c0 1.05 2.24 1.9 5 1.9s5-.85 5-1.9V3.8" {...stroke} />
      <path d="M3 8c0 1.05 2.24 1.9 5 1.9S13 9.05 13 8" {...stroke} />
    </Icon>
  );
}
