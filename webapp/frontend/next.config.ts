import { existsSync, readFileSync } from "node:fs";
import path from "node:path";

import type { NextConfig } from "next";

const staticExport = process.env.AIMING_COOKIE_STATIC_EXPORT === "1";

/**
 * 基础设施域名构建期注入：读仓库根 infra-urls.local.txt（gitignore 的本地文件，
 * KEY=VALUE 每行一对），经下方 env 键内联为 NEXT_PUBLIC_*。缺文件/缺键一律保持
 * example.invalid 占位回落——公开源码构建即此形态，构建不因缺本地文件失败。
 * tauri 打包会把本文件复制进 .tauri-static/ 再构建，故沿目录向上查找仓库根，
 * 不写死相对层级。
 */
const INFRA_ENV_FALLBACKS: Record<string, string> = {
  NEXT_PUBLIC_ACCOUNTS_BASE_URL: "https://accounts.example.invalid",
  NEXT_PUBLIC_MEMBER_GATEWAY_BASE_URL: "https://member-gateway.example.invalid:8443/member/v1",
  NEXT_PUBLIC_AFFILIATE_BASE_URL: "https://affiliate.example.invalid",
};

function loadInfraEnv(): Record<string, string> {
  const resolved = { ...INFRA_ENV_FALLBACKS };
  try {
    let dir = typeof __dirname !== "undefined" ? __dirname : process.cwd();
    for (let depth = 0; depth < 4; depth += 1) {
      const candidate = path.join(dir, "infra-urls.local.txt");
      if (existsSync(candidate)) {
        for (const line of readFileSync(candidate, "utf8").split(/\r?\n/)) {
          const trimmed = line.trim();
          const eq = trimmed.indexOf("=");
          if (trimmed.startsWith("#") || eq <= 0) continue;
          const key = `NEXT_PUBLIC_${trimmed.slice(0, eq).trim()}`;
          const value = trimmed.slice(eq + 1).trim();
          if (key in resolved && value) resolved[key] = value;
        }
        break;
      }
      const parent = path.dirname(dir);
      if (parent === dir) break;
      dir = parent;
    }
  } catch {
    // 读不到注入文件时保持占位回落（见上），不让构建失败。
  }
  return resolved;
}

const infraEnv = loadInfraEnv();

const staticConfig: NextConfig = {
  reactStrictMode: true,
  output: "export",
  trailingSlash: true,
  images: { unoptimized: true },
  env: infraEnv,
};

const serverConfig: NextConfig = {
  reactStrictMode: true,
  env: infraEnv,
  async rewrites() {
    if (process.env.AIMING_COOKIE_API_MODE === "mock") return [];
    return [
      {
        source: "/api/:path*",
        destination: "http://localhost:8000/api/:path*",
      },
    ];
  },
};

const nextConfig: NextConfig = staticExport ? staticConfig : serverConfig;

export default nextConfig;
