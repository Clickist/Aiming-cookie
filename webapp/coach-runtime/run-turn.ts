#!/usr/bin/env node
import { readFileSync } from "node:fs";

import { runCoachTurn } from "./src/turn.ts";
import { failureResponse, makeError } from "./src/contracts.ts";

async function readStdin(): Promise<string> {
  if (process.stdin.isTTY) {
    return "";
  }
  return readFileSync(0, "utf8");
}

async function main(): Promise<void> {
  const input = (await readStdin()).trim();
  if (!input) {
    const response = failureResponse(
      makeError({
        category: "coach_runtime",
        code: "empty_stdin",
        message: "Expected one JSON line on stdin",
        retryable: false,
      }),
    );
    process.stdout.write(`${JSON.stringify(response)}\n`);
    process.exitCode = 1;
    return;
  }

  let parsed: unknown;
  try {
    parsed = JSON.parse(input);
  } catch {
    const response = failureResponse(
      makeError({
        category: "coach_runtime",
        code: "invalid_json",
        message: "stdin is not valid JSON",
        retryable: false,
      }),
    );
    process.stdout.write(`${JSON.stringify(response)}\n`);
    process.exitCode = 1;
    return;
  }

  const response = await runCoachTurn(parsed);
  process.stdout.write(`${JSON.stringify(response)}\n`);
  if (!response.ok) {
    process.exitCode = 1;
  }
}

main().catch((error) => {
  const response = failureResponse(
    makeError({
      category: "coach_runtime",
      code: "unhandled",
      message: error instanceof Error ? error.message : String(error),
      retryable: false,
    }),
  );
  process.stdout.write(`${JSON.stringify(response)}\n`);
  process.exitCode = 1;
});