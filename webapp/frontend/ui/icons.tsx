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
