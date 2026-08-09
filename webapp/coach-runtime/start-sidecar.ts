#!/usr/bin/env node
import {
  DEFAULT_SIDECAR_HOST,
  DEFAULT_SIDECAR_PORT,
  startSidecarServer,
} from "./src/sidecar-server.ts";

const port = Number(process.env.COACH_SIDECAR_PORT ?? String(DEFAULT_SIDECAR_PORT));
const host = process.env.COACH_SIDECAR_HOST ?? DEFAULT_SIDECAR_HOST;

const server = startSidecarServer({ host, port });

server.on("listening", () => {
  const address = server.address();
  const bound =
    typeof address === "object" && address !== null
      ? `http://${host}:${address.port}`
      : `http://${host}:${port}`;
  process.stderr.write(`coach sidecar listening on ${bound}\n`);
});

server.on("error", (error) => {
  process.stderr.write(
    `coach sidecar failed: ${error instanceof Error ? error.message : String(error)}\n`,
  );
  process.exitCode = 1;
});
