import { expect } from "vitest";
import { describeEval, toolCalls } from "vitest-evals";
import { createPiCodingAgentHarness } from "./pi-harness.ts";

const piExtensionsHarness = createPiCodingAgentHarness({
	name: "pi-coding-agent-extensions",
	output: ({ response, session }) => {
		return {
			response,
			extensionPaths: session.extensionRunner.getExtensionPaths(),
			toolDefinitions: session.getAllTools().map(({ name, description }) => ({ name, description })),
		};
	},
});

describeEval("Pi extensions", { harness: piExtensionsHarness }, (it) => {
	it("creates, reloads, and uses a hello extension", async ({ run }) => {
		const result = await run([
			{
				type: "prompt",
				content:
					"Create a Pi extension with a hello tool that takes a name and returns a greeting. For example, passing Bob should return `Hello, Bob!`.",
			},
			{ type: "reload" },
			{
				type: "prompt",
				content: "Use the hello tool to greet Bob.",
			},
		]);

		expect(result.output.response).toBe("Hello, Bob!");
		expect(result.output.extensionPaths).toHaveLength(1);
		expect(result.output.toolDefinitions).toContainEqual(expect.objectContaining({ name: "hello" }));
		expect(result.errors).toEqual([]);
		expect(toolCalls(result.session)).toContainEqual({
			name: "hello",
			arguments: { name: "Bob" },
			status: "ok",
			result: "Hello, Bob!",
		});
	});
});
