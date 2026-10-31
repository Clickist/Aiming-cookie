import assert from "node:assert/strict";
import { test } from "node:test";
import { act, create, type ReactTestRenderer } from "react-test-renderer";

import { CoachPresentationText } from "../components/task7/CoachPresentationText";
import { presentationFromText, type CoachGenerationStatus } from "../lib/openui/presentation";
import { readFrontendLog, resetFrontendLog } from "../lib/frontend-log";
import { openUIDiagnosticLine } from "../lib/openui/diagnostics";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function reply(source: string, generationStatus: CoachGenerationStatus = "completed", revision = 1) {
  return presentationFromText(`讲解\n\`\`\`openui-lang\n${source}\n\`\`\``, {
    messageId: 42,
    generationStatus,
    revision,
  });
}

const validSource = 'root = Stack([c, buttons])\nc = Card([TextContent("实际图卡")])\nbuttons = Buttons([Button("继续看", @ToAssistant("继续看"))])';

test("挂载和流式更新后的解析结果不被父级 effect 清掉，完成后按钮可用", async () => {
  let renderer!: ReactTestRenderer;
  const actions: unknown[] = [];
  const onToAssistant = (action: unknown) => { actions.push(action); };
  await act(async () => {
    renderer = create(<CoachPresentationText presentation={reply(validSource, "streaming")} onToAssistant={onToAssistant} />);
  });
  assert.equal(renderer.root.findAllByType("button").length, 1);
  assert.ok(renderer.root.findAllByType("button").every((button) => button.props.disabled));

  await act(async () => {
    renderer.update(<CoachPresentationText presentation={reply(`${validSource}\n`, "streaming", 2)} onToAssistant={onToAssistant} />);
  });
  await act(async () => {
    renderer.update(<CoachPresentationText presentation={reply(`${validSource}\n`, "completed", 3)} onToAssistant={onToAssistant} />);
  });
  const root = renderer.root.findByProps({ className: "coach-openui-root" });
  assert.equal(root.props["data-openui-state"], "ready");
  const button = renderer.root.findByType("button");
  assert.equal(button.props.disabled, false);
  assert.equal(actions.length, 0);
  await act(async () => button.props.onClick());
  assert.equal(actions.length, 1);
  assert.equal((actions[0] as { message: string }).message, "继续看");
  await act(async () => renderer.unmount());
});

test("无 root 的真实失败不会永远卡在 rendering，后续新 revision 可以恢复", async () => {
  resetFrontendLog();
  let renderer!: ReactTestRenderer;
  await act(async () => {
    renderer = create(<CoachPresentationText presentation={reply('Stack([TextContent("无入口")])')} />);
  });
  assert.equal(renderer.root.findByProps({ className: "coach-openui-notice" }).props["data-cause"], "parse_error");
  const failures = readFrontendLog().filter((entry) => entry.source === "openui.presentation");
  assert.ok(failures.length >= 1);
  const diagnostic = JSON.parse(failures.at(-1)!.message);
  assert.equal(diagnostic.message_id, 42);
  assert.equal(diagnostic.cause, "parse_error");
  assert.ok(diagnostic.codes.includes("no-root"));
  assert.doesNotMatch(failures.at(-1)!.message, /无入口|TextContent|Stack\(/);
  const count = failures.length;
  await act(async () => renderer.update(<CoachPresentationText presentation={reply('Stack([TextContent("无入口")])')} />));
  assert.equal(readFrontendLog().filter((entry) => entry.source === "openui.presentation").length, count);
  await act(async () => {
    renderer.update(<CoachPresentationText presentation={reply(validSource, "completed", 2)} />);
  });
  assert.equal(renderer.root.findByProps({ className: "coach-openui-root" }).props["data-openui-state"], "ready");
  await act(async () => renderer.unmount());
});

test("调用方终态覆盖旧 streaming 快照，中断不冒充解析错误", async () => {
  let renderer!: ReactTestRenderer;
  const snapshot = reply(validSource, "streaming");
  await act(async () => {
    renderer = create(<CoachPresentationText presentation={snapshot} generationStatus="failed" onToAssistant={() => undefined} />);
  });
  assert.equal(renderer.root.findByProps({ className: "coach-openui-root" }).props["data-openui-state"], "degraded");
  assert.equal(renderer.root.findByType("button").props.disabled, true);
  const log = JSON.parse(readFrontendLog().at(-1)!.message);
  assert.equal(log.generation_status, "failed");
  assert.equal(log.stage, "generation");
  await act(async () => renderer.unmount());
});

test("其它回合运行中禁用历史追问，超长动作也不会发请求", async () => {
  let renderer!: ReactTestRenderer;
  const actions: unknown[] = [];
  const onToAssistant = (action: unknown) => { actions.push(action); };
  await act(async () => {
    renderer = create(<CoachPresentationText presentation={reply(validSource)} actionsDisabled onToAssistant={onToAssistant} />);
  });
  assert.equal(renderer.root.findByType("button").props.disabled, true);
  await act(async () => renderer.root.findByType("button").props.onClick());
  assert.equal(actions.length, 0);
  const longSource = `root = Stack([Button("继续", @ToAssistant("${"x".repeat(2001)}"))])`;
  await act(async () => renderer.update(<CoachPresentationText presentation={reply(longSource, "completed", 2)} onToAssistant={onToAssistant} />));
  assert.equal(renderer.root.findByType("button").props.disabled, true);
  await act(async () => renderer.root.findByType("button").props.onClick());
  assert.equal(actions.length, 0);
  await act(async () => renderer.unmount());
});

test("诊断只记录定位字段和错误码，不复制源代码或异常内容", () => {
  const line = openUIDiagnosticLine({
    conversation_id: 182, message_id: 10, run_id: "run:test", segment_id: "seg:1",
    revision: 2, generation_status: "completed", state: "failed", cause: "render_error",
    errors: [{ code: "render-error", message: "sk-secret-jwt-and-private-content" }, { code: "private-value" }],
  });
  const data = JSON.parse(line);
  assert.deepEqual(data.codes, ["render-error", "unknown-error"]);
  assert.equal(data.stage, "render");
  assert.doesNotMatch(line, /sk-secret|jwt-and-private|private-value/);
});
