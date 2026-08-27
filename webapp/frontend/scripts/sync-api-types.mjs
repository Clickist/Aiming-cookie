#!/usr/bin/env node
/**
 * 前后端类型合同单一事实源同步脚本。
 *
 * 流程：用仓库根 .venv 的 Python 导出 FastAPI OpenAPI schema（隔离 DATA_ROOT，
 * 不触碰真实数据），再用 openapi-typescript 生成 lib/api-types.generated.ts。
 *
 * 用法：
 *   node scripts/sync-api-types.mjs           # 重新生成并覆盖 api-types.generated.ts
 *   node scripts/sync-api-types.mjs --check   # 只校验入库文件与当前 schema 一致，不一致退出码 1
 */
import { spawnSync } from "node:child_process";
import { tmpdir } from "node:os";
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const frontendDir = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const repoRoot = resolve(frontendDir, "..", "..");
const outputPath = join(frontendDir, "lib", "api-types.generated.ts");

const pythonExe =
  process.platform === "win32"
    ? join(repoRoot, ".venv", "Scripts", "python.exe")
    : join(repoRoot, ".venv", "bin", "python");
if (!existsSync(pythonExe)) {
  console.error(`[sync-api-types] 未找到仓库虚拟环境解释器：${pythonExe}`);
  process.exit(2);
}

const exportSchemaTo = (jsonPath) => {
  const pythonCode = `
import json, os
from webapp.backend.app import app
with open(os.environ["AIMING_OPENAPI_OUT"], "w", encoding="utf-8", newline="\\n") as f:
    json.dump(app.openapi(), f, ensure_ascii=False, indent=2, sort_keys=True)
`;
  // DATA_ROOT / KOVAAK_INSTALL_DIR 按 docs/DEVELOPMENT.md 惯例隔离，导入 backend 无副作用。
  const dataRoot = mkdtempSync(join(jsonPath, "aiming-sync-data-"));
  const result = spawnSync(
    pythonExe,
    ["-c", pythonCode],
    {
      cwd: repoRoot,
      env: {
        ...process.env,
        AIMING_OPENAPI_OUT: join(jsonPath, "openapi.json"),
        DATA_ROOT: dataRoot,
        KOVAAK_INSTALL_DIR: join(dataRoot, "missing-kovaak"),
      },
      encoding: "utf8",
    },
  );
  rmSync(dataRoot, { recursive: true, force: true });
  if (result.error) throw result.error;
  if (result.status !== 0) {
    console.error(`[sync-api-types] 导出 OpenAPI schema 失败：\n${result.stderr}`);
    process.exit(2);
  }
};

const generateTypes = async (jsonPath) => {
  const { default: openapiTS, astToString } = await import("openapi-typescript");
  const ast = await openapiTS(pathToFileURL(jsonPath));
  // 统一 LF 行尾，保证跨平台字节级一致（--check 同步测试依赖它）。
  return `${astToString(ast).replace(/\r\n/g, "\n")}`;
};

const check = process.argv.includes("--check");
// 临时目录一律落系统 tmp：回退 repoRoot 会在并发测试窗把泄漏物丢进仓库根。
const tempDir = mkdtempSync(join(tmpdir(), "aiming-openapi-sync-"));
try {
  exportSchemaTo(tempDir);
  const jsonPath = join(tempDir, "openapi.json");
  const generated = await generateTypes(jsonPath);

  if (check) {
    if (!existsSync(outputPath)) {
      console.error(`[sync-api-types] 缺少 ${outputPath}；请运行 npm run sync-types 重新生成。`);
      process.exit(1);
    }
    const committed = readFileSync(outputPath, "utf8").replace(/\r\n/g, "\n");
    if (committed !== generated) {
      console.error("[sync-api-types] lib/api-types.generated.ts 与当前后端 OpenAPI schema 不一致；请运行 npm run sync-types 重新生成。");
      process.exit(1);
    }
    console.log("[sync-api-types] 已与后端 OpenAPI schema 保持一致。");
    process.exit(0);
  }

  writeFileSync(outputPath, generated.replace(/\r\n/g, "\n"), { encoding: "utf8" });
  const schema = JSON.parse(readFileSync(jsonPath, "utf8"));
  console.log(
    `[sync-api-types] 已生成 ${outputPath}（paths=${Object.keys(schema.paths ?? {}).length}, schemas=${Object.keys(schema.components?.schemas ?? {}).length}）`,
  );
} finally {
  rmSync(tempDir, { recursive: true, force: true });
}
