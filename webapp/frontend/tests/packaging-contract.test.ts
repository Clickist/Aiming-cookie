import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { existsSync } from "node:fs";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { promisify } from "node:util";
import { test } from "node:test";

const frontendRoot = path.resolve(import.meta.dirname, "..");
const repoRoot = path.resolve(frontendRoot, "..", "..");
const invariantChecker = path.join(repoRoot, "scripts", "check-frontend-invariants.mjs");
const execFileAsync = promisify(execFile);

async function source(relativePath: string): Promise<string> {
  return readFile(path.join(frontendRoot, relativePath), "utf8");
}

test("Tauri embeds the static export and enables the NSIS bundle", async () => {
  const config = JSON.parse(await source("src-tauri/tauri.conf.json"));
  assert.equal(config.build.frontendDist, "../out");
  assert.equal(config.bundle.active, true);
  assert.deepEqual(config.bundle.targets, ["nsis"]);
  assert.equal(config.app.windows.length, 1);
  assert.equal(config.app.windows[0].label, "main");
});

test("Tauri package uses the app toolbar instead of a native title bar", async () => {
  const config = JSON.parse(await source("src-tauri/tauri.conf.json"));
  const capability = JSON.parse(await source("src-tauri/capabilities/default.json"));
  assert.equal(config.app.windows[0].decorations, false);
  // 0910 圆角窗口：is-maximized 供前端在最大化时收回圆角与自绘阴影。
  assert.deepEqual(capability.permissions.filter((permission: string) => permission.startsWith("core:window:")), [
    "core:window:allow-close",
    "core:window:allow-is-maximized",
    "core:window:allow-minimize",
    "core:window:allow-start-dragging",
    "core:window:allow-toggle-maximize",
  ]);
});

test("Next production build is a static export", async () => {
  const config = await source("next.config.ts");
  assert.match(config, /output:\s*"export"/);
  assert.match(config, /images:\s*\{\s*unoptimized:\s*true/);
});

test("Coach session route is a static-export-compatible single page", async () => {
  const page = await source("app/s/page.tsx");
  assert.doesNotMatch(page, /generateStaticParams|\[sessionId\]/);
  assert.match(page, /CoachWorkspacePage/);
  assert.ok(!existsSync(path.join(frontendRoot, "app", "s", "[sessionId]")),
    "dynamic [sessionId] directory must not exist");
});

test("build:tauri emits out/index.html and the static /s shell", { timeout: 240_000 }, async () => {
  await execFileAsync("npm.cmd", ["run", "build:tauri"], {
    cwd: frontendRoot,
    timeout: 200_000,
    maxBuffer: 10 * 1024 * 1024,
    shell: true,
  });
  assert.ok(existsSync(path.join(frontendRoot, "out", "index.html")),
    "out/index.html must exist after build:tauri");
  assert.ok(existsSync(path.join(frontendRoot, "out", "s", "index.html")),
    "out/s/index.html must exist after build:tauri");
  // Independent of the build script's own gate: assert the invariant on the real out/.
  await execFileAsync(process.execPath, [invariantChecker], {
    cwd: frontendRoot,
    maxBuffer: 10 * 1024 * 1024,
  });
});

// 回归护栏（2026-09-20 实锤）：staging 目录缺 .env* 时 NEXT_PUBLIC_* 不会内联，
// 打包版诊断包上传会静默降级。build:tauri 必须在构建后跑该不变量校验。
test("build:tauri gates on the frontend invariant checker", { timeout: 240_000 }, async () => {
  const script = await source("../../scripts/build-tauri-frontend.ps1");
  assert.match(script, /check-frontend-invariants\.mjs/,
    "build:tauri must run scripts/check-frontend-invariants.mjs after the build");
  assert.match(script, /Frontend build invariant check failed/,
    "the invariant checker must fail the build, not warn");
  assert.match(script, /\.env\.production\.local/,
    "the build must mirror production env files into the staging build dir");
  assert.match(script, /aiming-cookie-desktop-\*/,
    "the build must purge the tauri asset embed cache");
  const pkg = JSON.parse(await source("package.json"));
  assert.equal(pkg.scripts["check:frontend-invariants"],
    "node ../../scripts/check-frontend-invariants.mjs");
});

// 校验脚本自身要能独立运行：既能在已内联的产物上通过，也能在“运行时读取 env”的
// 产物上失败（否则它只是一条永远绿灯的摆设）。
test("frontend invariant checker detects uninlined NEXT_PUBLIC_* chunks", { timeout: 60_000 }, async () => {
  const fixtureRoot = await mkdtemp(path.join(tmpdir(), "ac-frontend-invariants-"));
  const chunks = path.join(fixtureRoot, "_next", "static", "chunks");
  await mkdir(chunks, { recursive: true });

  try {
    // 未内联：chunk 里残留 env 符号，头部取值来自运行时表达式。
    const staleChunk = path.join(chunks, "stale.js");
    await writeFile(
      staleChunk,
      'let w=e.default.env.NEXT_PUBLIC_DIAGNOSTICS_UPLOAD_TOKEN??"";fetch(u,{headers:{"X-AC-Token":w}})',
      "utf8",
    );
    await assert.rejects(
      execFileAsync(process.execPath, [invariantChecker, fixtureRoot], { cwd: frontendRoot }),
      (error: Error & { code?: number }) => error.code === 1,
      "the checker must exit 1 when the env symbol is still present",
    );

    // 已内联：env 符号消失，头部取值是字符串字面量。
    await rm(staleChunk, { force: true });
    await writeFile(
      path.join(chunks, "fresh.js"),
      'fetch(u,{headers:{"X-AC-Token":"0123456789012345678901234567890123456789012345"}})',
      "utf8",
    );
    const { stdout, stderr } = await execFileAsync(process.execPath, [invariantChecker, fixtureRoot], { cwd: frontendRoot });
    assert.match(`${stdout}${stderr}`, /已内联/);
  } finally {
    await rm(fixtureRoot, { recursive: true, force: true });
  }
});

test("legacy analysis routes stay bounded compatibility redirects", async () => {
  const shell = await source("app/analysis/page.tsx");
  const legacy = await source("app/analysis/[analysisId]/page.tsx");
  assert.match(shell, /redirect\("\/history"\)/);
  assert.match(legacy, /redirect\("\/history"\)/);
  assert.match(legacy, /generateStaticParams/);
});

test("Windows packaging keeps signing explicit and resource builds source-independent", async () => {
  const buildScript = await source("../../scripts/build-windows-installer.ps1");
  const runtimeScript = await source("../../scripts/build-windows-runtime.ps1");
  assert.match(buildScript, /Unsigned mode is explicit/);
  assert.match(buildScript, /CertificateThumbprint/);
  assert.match(buildScript, /Get-AuthenticodeSignature/);
  assert.match(runtimeScript, /PyInstaller/);
  assert.match(runtimeScript, /--compile/);
  assert.match(runtimeScript, /coach-system\.md/);
});

test("desktop startup is single-instance and focuses the existing main window", async () => {
  const rustSource = await source("src-tauri/src/lib.rs");
  assert.match(rustSource, /tauri_plugin_single_instance::init/);
  assert.match(rustSource, /get_webview_window\("main"\)/);
  assert.match(rustSource, /window\.show\(\)/);
  assert.match(rustSource, /window\.set_focus\(\)/);
});
