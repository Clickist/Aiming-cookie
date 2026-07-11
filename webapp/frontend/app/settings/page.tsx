import Link from "next/link";
import { ThemePreferenceSelect } from "../../components/ThemeController";

export default function SettingsPage() {
  return (
    <main id="main-content" className="min-h-dvh bg-background px-md py-xl">
      <div className="mx-auto w-full max-w-xl">
        <Link
          href="/"
          className="text-label-md text-on-surface-variant transition-colors hover:text-primary"
        >
          ← 返回分析首页
        </Link>

        <section className="mt-lg border border-outline bg-surface-container-low rounded-lg p-md">
          <p className="font-mono text-label-sm text-primary">SETTINGS</p>
          <h1 className="mt-xs font-display text-headline-md text-on-surface">
            外观
          </h1>
          <p className="mt-xs text-body-md text-on-surface-variant">
            为本机的 Aiming Cookie Desktop 选择显示主题。
          </p>

          <div className="mt-md border-t border-outline pt-md">
            <ThemePreferenceSelect />
          </div>
        </section>
      </div>
    </main>
  );
}
