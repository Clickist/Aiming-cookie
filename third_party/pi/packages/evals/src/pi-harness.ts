import assert from "node:assert";
import { mkdir, mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { contentText } from "@earendil-works/pi-ai";
import {
	type AgentSession,
	type CreateAgentSessionOptions,
	createAgentSessionFromServices,
	createAgentSessionServices,
	ModelRuntime,
	SessionManager,
	SettingsManager,
} from "@earendil-works/pi-coding-agent";
import {
	createHarness,
	type Harness,
	type JsonValue,
	normalizeRecord,
	type SimpleHarnessResult,
	type TranscriptEvent,
	toJsonValue,
} from "vitest-evals/harness";

type PiCodingAgentInput = string | Array<{ type: "prompt"; content: string } | { type: "reload" }>;

type PiCodingAgentHarnessOptions = {
	name?: string;
	noTools?: CreateAgentSessionOptions["noTools"];
};

type PiCodingAgentHarnessWithOutput<TOutput extends JsonValue> = PiCodingAgentHarnessOptions & {
	output: (args: { response: string; session: AgentSession }) => TOutput | Promise<TOutput>;
};

function getRequiredModelSelection(): { provider: string; model: string } {
	const provider = process.env.PI_PROVIDER?.trim();
	const model = process.env.PI_MODEL?.trim();
	if (!provider || !model) throw new Error("PI_PROVIDER and PI_MODEL must both be set for eval runs.");
	return { provider, model };
}

function toTranscriptEvents(messages: AgentSession["messages"]): TranscriptEvent[] {
	const events: TranscriptEvent[] = [];
	for (const message of messages) {
		if (message.role === "user") {
			events.push({ type: "message", role: "user", content: contentText(message.content) });
		} else if (message.role === "assistant") {
			const text = contentText(message.content);
			if (text) events.push({ type: "message", role: "assistant", content: text });
			for (const part of message.content) {
				if (part.type === "toolCall") {
					events.push({
						type: "tool_call",
						id: part.id,
						name: part.name,
						arguments: normalizeRecord(part.arguments),
					});
				}
			}
		} else if (message.role === "toolResult") {
			const text = contentText(message.content);
			events.push({
				type: "tool_result",
				toolCallId: message.toolCallId,
				name: message.toolName,
				content: message.content.every((part) => part.type === "text") ? text : toJsonValue(message.content),
				...(message.isError ? { error: { message: text || "Tool failed" } } : {}),
			});
		}
	}
	return events;
}

async function promptAgent(session: AgentSession, input: string, signal: AbortSignal | undefined): Promise<string> {
	signal?.throwIfAborted();
	const previousMessageCount = session.messages.length;
	await session.prompt(input);
	const assistant = session.messages
		.slice(previousMessageCount)
		.reverse()
		.find((message) => message.role === "assistant");
	if (!assistant) throw new Error("Agent run completed without an assistant message.");
	if (assistant.stopReason !== "stop") {
		throw new Error(
			assistant.errorMessage ?? `Agent run ended with unexpected stop reason: ${assistant.stopReason}.`,
		);
	}
	const output = session.getLastAssistantText();
	if (!output) throw new Error("Agent run produced no assistant text.");
	return output;
}

async function runPiCodingAgent<TOutput extends JsonValue>(
	input: PiCodingAgentInput,
	signal: AbortSignal | undefined,
	options: PiCodingAgentHarnessOptions | PiCodingAgentHarnessWithOutput<TOutput>,
): Promise<SimpleHarnessResult<string | TOutput>> {
	signal?.throwIfAborted();
	const selection = getRequiredModelSelection();
	const modelRuntime = await ModelRuntime.create();
	const model = modelRuntime.getModel(selection.provider, selection.model);
	if (!model) throw new Error(`Eval model not found: ${selection.provider}/${selection.model}`);

	const root = await mkdtemp(join(tmpdir(), "pi-eval-"));
	const cwd = join(root, "workspace");
	const agentDir = join(root, "agent");
	let session: AgentSession | undefined;
	let outcome: { success: true; result: SimpleHarnessResult<string | TOutput> } | { success: false; error: unknown };
	try {
		await Promise.all([mkdir(cwd), mkdir(agentDir)]);
		const services = await createAgentSessionServices({
			cwd,
			agentDir,
			modelRuntime,
			settingsManager: SettingsManager.inMemory(),
		});
		signal?.throwIfAborted();
		session = (
			await createAgentSessionFromServices({
				services,
				sessionManager: SessionManager.inMemory(cwd),
				model,
				thinkingLevel: "off",
				noTools: options.noTools,
			})
		).session;

		const evalSession = session;
		const abort = () => void evalSession.abort();
		signal?.addEventListener("abort", abort, { once: true });
		try {
			signal?.throwIfAborted();
			assert.strictEqual(
				evalSession.extensionRunner.getExtensionPaths().length,
				0,
				"Expected an isolated eval session to start without extensions",
			);
			const steps = typeof input === "string" ? [{ type: "prompt" as const, content: input }] : input;
			let response: string | undefined;
			for (const step of steps) {
				if (step.type === "prompt") {
					response = await promptAgent(evalSession, step.content, signal);
				} else {
					await evalSession.reload();
				}
			}
			if (response === undefined) throw new Error("Pi eval input must include at least one prompt step.");
			const output = "output" in options ? await options.output({ response, session: evalSession }) : response;
			const stats = evalSession.getSessionStats();
			outcome = {
				success: true,
				result: {
					output,
					events: toTranscriptEvents(evalSession.messages),
					usage: {
						provider: model.provider,
						model: model.id,
						inputTokens: stats.tokens.input,
						outputTokens: stats.tokens.output,
						totalTokens: stats.tokens.total,
						toolCalls: stats.toolCalls,
					},
				},
			};
		} finally {
			signal?.removeEventListener("abort", abort);
		}
	} catch (error) {
		outcome = { success: false, error };
	}

	const cleanupErrors: unknown[] = [];
	try {
		session?.dispose();
	} catch (error) {
		cleanupErrors.push(error);
	}
	try {
		await rm(root, { recursive: true, force: true });
	} catch (error) {
		cleanupErrors.push(error);
	}

	if (!outcome.success) {
		if (cleanupErrors.length === 0) throw outcome.error;
		throw new AggregateError([outcome.error, ...cleanupErrors], "Agent run failed and cleanup also failed.");
	}
	if (cleanupErrors.length === 1) throw cleanupErrors[0];
	if (cleanupErrors.length > 1) throw new AggregateError(cleanupErrors, "Agent cleanup failed.");
	return outcome.result;
}

export function createPiCodingAgentHarness<TOutput extends JsonValue>(
	options: PiCodingAgentHarnessWithOutput<TOutput>,
): Harness<PiCodingAgentInput, TOutput>;
export function createPiCodingAgentHarness(options?: PiCodingAgentHarnessOptions): Harness<PiCodingAgentInput, string>;
export function createPiCodingAgentHarness<TOutput extends JsonValue>(
	options: PiCodingAgentHarnessOptions | PiCodingAgentHarnessWithOutput<TOutput> = {},
) {
	return createHarness<PiCodingAgentInput, string | TOutput>({
		name: options.name ?? "pi-coding-agent",
		run: ({ input, signal }) => runPiCodingAgent(input, signal, options),
	});
}
