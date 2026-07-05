/**
 * RSC Suspense fallback for the coach route.
 *
 * Coach page is a server component that fetches /api/sessions/{id} +
 * chat history before render — this minimal skeleton keeps the layout
 * stable (header + centered loading cue) instead of flashing blank.
 */
export default function CoachLoading() {
  return (
    <div className="h-dvh flex flex-col bg-background text-on-surface">
      <header className="flex justify-between items-center px-md py-sm bg-background border-b border-outline">
        <span className="font-mono text-headline-sm font-extrabold text-primary tracking-tight">
          Aiming Cookie
        </span>
        <span className="text-label-md text-on-surface-variant">加载对话…</span>
      </header>
      <main id="main-content" className="flex-1 flex items-center justify-center">
        <div className="text-center">
          <span className="material-symbols-outlined text-[48px] text-primary animate-pulse inline-block">
            psychology
          </span>
          <p className="text-body-md text-on-surface-variant mt-md">教练准备中…</p>
        </div>
      </main>
    </div>
  );
}
