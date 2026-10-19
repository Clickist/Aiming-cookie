#!/usr/bin/env node
// 手动开发启动入口：桌面令牌闸门 fail-closed（healthz 之外一律 401），
// 自起时必须自带 AIMING_COOKIE_DESKTOP_TOKEN（与 backend 的同 env 一致），如：
//   AIMING_COOKIE_DESKTOP_TOKEN=dev-secret bun start-sidecar.ts
// 桌面模式下由 Tauri 每次启动生成新 token 注入，不经本入口。
// 首条 import：代理感知 fetch 必须先于任何其他模块体安装（issue#8——
// Bun --compile 产物的全局 fetch 不读 HTTPS_PROXY，xAI 等 OAuth 直连被墙）。
import "./src/proxy-fetch.ts";

// issue#8 第二层：pi 的 OAuth 流程默认走运行时动态 import（load.ts 用变量
// specifier 故意防打包），compile 产物里没有源文件会 "Cannot find module"。
// 与 pi 自家 Bun CLI（coding-agent/src/bun/cli.ts）同法：Bun 运行时下注册
// 静态嵌入的 loaders；dev（node+tsx）下保持动态 import 从源码解析。
import { registerBunOAuthFlows } from "../../third_party/pi/packages/ai/src/bun-oauth.ts";

if ((globalThis as { Bun?: unknown }).Bun !== undefined) {
  registerBunOAuthFlows();
}

import {
  DEFAULT_SIDECAR_HOST,
  DEFAULT_SIDECAR_PORT,
  startSidecarServer,
} from "./src/sidecar-server.ts";
import { ensureAppDataDirs } from "./src/app-data.ts";
import { installProcessGuards } from "./src/process-guards.ts";

// 进程级兜底（鲁棒性审计 P1-12）：unhandledRejection 落日志存活、
// uncaughtException 落日志退出——kill 落在会话 JSONL appendFile 中途正是
// 撕裂行（P0-1）的直接放大器，先把"隐形自杀"变"有痕降级"。
installProcessGuards();

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
