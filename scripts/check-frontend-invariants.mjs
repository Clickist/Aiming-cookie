#!/usr/bin/env node
/**
 * Tauri 静态前端产物（out/）的构建期不变量校验。
 *
 * 背景：诊断包直传的 token 走 `NEXT_PUBLIC_DIAGNOSTICS_UPLOAD_TOKEN`，由 Next 在
 * 构建期从构建目录的 `.env.production.local`（gitignore）内联进客户端 chunk。
 * 一旦构建期没读到 env 文件，chunk 里会留下 `process.env.NEXT_PUBLIC_...` 的运行时
 * 读取（取到空串），打包版点击「上传诊断包」就会静默降级成本地导出。
 *
 * 本脚本断言（不打印 token 明文，只报长度）：
 *   1. 任何 chunk 里都不允许残留 `NEXT_PUBLIC_DIAGNOSTICS_UPLOAD_TOKEN` 符号（=未内联）；
 *   2. 上传代码存在：至少一个 chunk 含 `X-AC-Token` 请求头；
 *   3. 该请求头的取值可解析为非空字符串字面量（即 token 已内联）。
 *
 * 用法：
 *   node scripts/check-frontend-invariants.mjs [outDir]
 * 退出码：0 通过；1 违反不变量；2 用法/IO 错误（如 out/ 不存在）。
 */
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const defaultOutDir = join(repoRoot, "webapp", "frontend", "out");

const ENV_SYMBOL = "NEXT_PUBLIC_DIAGNOSTICS_UPLOAD_TOKEN";
const HEADER_NAME = "X-AC-Token";
const TAG = "[check-frontend-invariants]";

const outDir = resolve(process.argv[2] ?? defaultOutDir);
const chunksDir = join(outDir, "_next", "static", "chunks");

if (!existsSync(chunksDir)) {
  console.error(`${TAG} 找不到 chunk 目录：${chunksDir}`);
  console.error(`${TAG} 先跑一次前端构建（cd webapp/frontend && npm run build:tauri）再来校验。`);
  process.exit(2);
}

function listChunkFiles(dir) {
  const files = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) files.push(...listChunkFiles(full));
    else if (entry.isFile() && entry.name.endsWith(".js")) files.push(full);
  }
  return files;
}

/** 从 `source[start]` 起读一个 JS 值：字符串字面量或标识符/成员表达式。 */
function readJsValue(source, start) {
  let i = start;
  while (i < source.length && /\s/.test(source[i])) i += 1;
  const quote = source[i];
  if (quote === '"' || quote === "'" || quote === "`") {
    let value = "";
    i += 1;
    while (i < source.length) {
      const ch = source[i];
      if (ch === "\\") {
        value += source[i + 1] ?? "";
        i += 2;
        continue;
      }
      if (ch === quote) {
        i += 1;
        break;
      }
      value += ch;
      i += 1;
    }
    return { kind: "literal", value, end: i };
  }
  const match = /^[A-Za-z_$][A-Za-z0-9_$.]*/.exec(source.slice(i));
  if (match) return { kind: "expression", value: match[0], end: i + match[0].length };
  return { kind: "unknown", value: source.slice(i, i + 24), end: i };
}

/**
 * 在同一个 chunk 里找 `ident = "..."` 的赋值，返回其字面量长度（找不到返回 null）。
 * best-effort：压缩器跨作用域复用短名时可能解析到无关字面量造成假通过；主判据
 * （ENV_SYMBOL 残留即违例）不受此影响，这里只做第二道参考信号。
 */
function resolveIdentifierLength(source, ident) {
  if (!/^[A-Za-z_$][A-Za-z0-9_$]*$/.test(ident)) return null;
  const pattern = new RegExp(`(?:^|[^\\w$.])${ident}\\s*=\\s*`, "g");
  let match;
  while ((match = pattern.exec(source)) !== null) {
    const value = readJsValue(source, match.index + match[0].length);
    if (value.kind === "literal") return value.value.length;
    if (value.kind === "expression") return null; // 仍是运行时取值（例如 e.default.env.X）
  }
  return null;
}

const chunkFiles = listChunkFiles(chunksDir).sort();
const violations = [];
const inlined = [];
let headerSeen = false;

for (const file of chunkFiles) {
  const relative = file.slice(outDir.length + 1).replace(/\\/g, "/");
  const source = readFileSync(file, "utf8");

  if (source.includes(ENV_SYMBOL)) {
    violations.push(
      `${relative}: 仍残留 ${ENV_SYMBOL} 运行时引用 —— 该构建没在构建期读到 .env.production.local（token 未内联）`,
    );
  }
  if (!source.includes(HEADER_NAME)) continue;

  const pattern = new RegExp(`["'\`]${HEADER_NAME}["'\`]\\s*:\\s*`, "g");
  let match;
  while ((match = pattern.exec(source)) !== null) {
    headerSeen = true;
    const value = readJsValue(source, match.index + match[0].length);
    if (value.kind === "literal") {
      if (value.value.length === 0) {
        violations.push(`${relative}: ${HEADER_NAME} 的值是空字符串（token 未内联）`);
      } else {
        inlined.push(`${relative}: ${HEADER_NAME} 已内联，值长度 ${value.value.length}`);
      }
      continue;
    }
    if (value.kind === "expression") {
      const length = resolveIdentifierLength(source, value.value);
      if (length === null) {
        violations.push(
          `${relative}: ${HEADER_NAME} 的取值来自运行时表达式（${value.value.slice(0, 24)}…）—— token 未内联`,
        );
      } else if (length === 0) {
        violations.push(`${relative}: ${HEADER_NAME} 的值是空字符串（token 未内联）`);
      } else {
        inlined.push(`${relative}: ${HEADER_NAME} 已内联，值长度 ${length}`);
      }
      continue;
    }
    violations.push(`${relative}: ${HEADER_NAME} 的取值不可解析（${value.value}）`);
  }
}

if (!headerSeen && violations.length === 0) {
  violations.push(`${HEADER_NAME} 未出现在任何 chunk —— 诊断包上传链路不在产物里`);
}

console.log(`${TAG} 扫描 ${chunkFiles.length} 个 chunk：${chunksDir}`);
for (const line of inlined) console.log(`  OK  ${line}`);
if (violations.length > 0) {
  console.error(`${TAG} 违反 ${violations.length} 项构建不变量：`);
  for (const line of violations) console.error(`  失败 ${line}`);
  console.error(
    `${TAG} 提示：确认 webapp/frontend/.env.production.local 存在且被构建目录读取（scripts/build-tauri-frontend.ps1 会把它镜像进 staging）。`,
  );
  process.exit(1);
}
console.log(`${TAG} OK：诊断包上传 token 已在构建期内联进客户端 chunk`);
