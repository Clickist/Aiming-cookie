import Link from "next/link";

/**
 * Custom 404 — branded "page not found" experience.
 *
 * redesign strategic omission: "No custom 404 page. Design a helpful, branded
 * 'page not found' experience." This replaces Next.js's default 404 with the
 * Aiming Cookie voice (瞄具/录像 比喻) + a clear way back to upload.
 */
export default function NotFound() {
  return (
    <div className="min-h-dvh flex items-center justify-center bg-background text-on-surface px-md">
      <div className="text-center max-w-md">
        <p className="font-display text-display-lg text-primary mb-sm">404</p>
        <h1 className="font-display text-headline-md text-on-surface mb-md">
          这一帧不在录像里
        </h1>
        <p className="text-body-lg text-on-surface-variant mb-lg">
          你访问的页面不存在,或已被归档。
        </p>
        <Link
          href="/"
          className="inline-flex items-center px-xl py-md bg-primary text-on-primary font-label-md rounded-md hover:brightness-110 active:scale-[0.98] transition-all"
        >
          返回上传
        </Link>
      </div>
    </div>
  );
}
