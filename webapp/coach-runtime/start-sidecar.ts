#!/usr/bin/env node
import {
  DEFAULT_SIDECAR_HOST,
  DEFAULT_SIDECAR_PORT,
  startSidecarServer,
} from "./src/sidecar-server.ts";
import { ensureAppDataDirs } from "./src/app-data.ts";

// Ensure app-data directory structure exists before the server starts.
ensureAppDataDirs();

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

// Desktop shutdown protocol (mirrors the Python runtime): when the host sets
// AIMING_COOKIE_WATCH_PARENT_STDIN it owns our stdin pipe and closes it on
// exit. Treat EOF as "shut down now" so the default path never needs the
// host's taskkill sweep. Manual dev runs without the flag are unaffected.
if (process.env.AIMING_COOKIE_WATCH_PARENT_STDIN === "1") {
  process.stdin.resume();
  process.stdin.once("end", () => {
    server.close();
    server.closeAllConnections?.();
    process.exit(0);
  });
}
