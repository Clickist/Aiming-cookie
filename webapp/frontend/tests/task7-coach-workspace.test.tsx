import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { test } from "node:test";

const root = path.resolve(import.meta.dirname, "..");

async function source(relativePath: string): Promise<string> {
  return readFile(path.join(root, relativePath), "utf8");
}

test("Fast Lane Coach workspace exposes the state and interaction contract", async () => {
  const component = await source("components/task7/CoachWorkspace.tsx");

  assert.match(component, /export type CoachWorkspaceStatus/);
  for (const state of ["waiting-provider", "capturing", "analysis", "missing-video", "failed", "completed"]) {
    assert.match(component, new RegExp(state));
  }
  assert.match(component, /messages: CoachWorkspaceMessage\[\]/);
  assert.match(component, /video: CoachWorkspaceVideoState/);
  assert.match(component, /evidenceSegments: CoachWorkspaceEvidenceSegment\[\]/);
  assert.match(component, /onSend: \(message: string\)/);
  assert.match(component, /onEvidenceSelect: \(segment: CoachWorkspaceEvidenceSegment\)/);
  assert.match(component, /onClick=\{\(\) => onEvidenceSelect\(segment\)\}/);
  assert.match(component, /<form[\s\S]*onSubmit/);
  assert.match(component, /<button[^>]+type="submit"/);
  assert.match(component, /aria-label=/);
  assert.doesNotMatch(component, /fetch\(|createCoachAgentRun|getCoach/);
  assert.doesNotMatch(component, /\.play\(|\.focus\(/);
});

test("Fast Lane workspace styling uses shared tokens and keeps the two-pane layout responsive", async () => {
  const styles = await source("components/task7/coach-workspace.css");

  assert.match(styles, /var\(--background\)/);
  assert.match(styles, /var\(--surface-container/);
  assert.match(styles, /var\(--on-surface/);
  assert.match(styles, /var\(--outline-variant\)/);
  assert.match(styles, /grid-template-columns/);
  assert.match(styles, /@media \(max-width: 840px\)/);
  assert.match(styles, /:focus-visible/);
  assert.match(styles, /data-state="failed"/);
  assert.match(styles, /data-state="missing-video"/);
  assert.doesNotMatch(styles, /#[0-9a-fA-F]{3,8}\b|\brgb\s*\(|\bhsl\s*\(/);
});
