import { CoachWorkspacePage } from "@/components/task7/CoachWorkspacePage";

export default function CoachSessionRoutePage() {
  // AppShell owns the single mounted CoachPanel for Coach routes.
  // The active session is resolved from the ?sessionId= query parameter at runtime.
  return <CoachWorkspacePage />;
}
