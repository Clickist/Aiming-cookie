import { redirect } from "next/navigation";

/**
 * Legacy session-scoped coach URL → persistent primary coach with attach.
 * Plan §2.5: thin redirect to `/coach?analysis=<id>`.
 */
export default async function CoachPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const sessionId = Number(id);
  if (!Number.isFinite(sessionId) || sessionId <= 0) {
    redirect("/");
  }
  redirect(`/coach?analysis=${sessionId}`);
}
