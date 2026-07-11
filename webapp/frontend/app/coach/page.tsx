import { Suspense } from "react";

import CoachClient from "./CoachClient";

export default function CoachPage() {
  return (
    <Suspense
      fallback={
        <main className="min-h-dvh flex items-center justify-center bg-background">
          <p className="text-label-md text-on-surface-variant">加载教练…</p>
        </main>
      }
    >
      <CoachClient />
    </Suspense>
  );
}
