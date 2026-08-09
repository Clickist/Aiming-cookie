import { CoachWorkspacePage } from "@/components/task7/CoachWorkspacePage";

export default async function CoachSessionPage({
  params,
}: {
  params: Promise<{ sessionId: string }>;
}) {
  const { sessionId: rawSessionId } = await params;
  const sessionId = Number(rawSessionId);

  return <CoachWorkspacePage sessionId={Number.isSafeInteger(sessionId) && sessionId > 0 ? sessionId : null} />;
}
