/**
 * RSC Suspense fallback for the report route.
 *
 * Next.js auto-wraps page.tsx in <Suspense fallback={<Loading />}> when a
 * loading.tsx sits next to it. The report page is a server component that
 * fetches /api/sessions/{id} before render — this skeleton shows structure
 * (hero + bento grid) so the user sees layout, not a blank frame.
 */
export default function ReportLoading() {
  return (
    <div className="min-h-dvh flex flex-col bg-background text-on-surface">
      <header className="flex justify-between items-center px-md py-sm bg-background border-b border-outline">
        <span className="font-mono text-headline-sm font-extrabold text-primary tracking-tight">
          Aiming Cookie
        </span>
        <span className="text-label-md text-on-surface-variant">加载报告…</span>
      </header>
      <main id="main-content" className="flex-grow pt-lg pb-32">
        <div className="max-w-[var(--spacing-container-max)] mx-auto px-md space-y-md">
          {/* hero skeleton */}
          <div className="glass-card p-xl h-48 animate-pulse" />
          {/* bento skeletons (8/4/7/5 mirroring ReportView) */}
          <div className="editorial-grid">
            <div className="col-span-12 md:col-span-8 glass-card p-xl h-64 animate-pulse" />
            <div className="col-span-12 md:col-span-4 glass-card p-md h-64 animate-pulse" />
            <div className="col-span-12 md:col-span-7 glass-card p-md h-48 animate-pulse" />
            <div className="col-span-12 md:col-span-5 glass-card p-md h-48 animate-pulse" />
          </div>
        </div>
      </main>
    </div>
  );
}
