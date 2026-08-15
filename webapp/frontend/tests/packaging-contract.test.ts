import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { existsSync } from "node:fs";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { promisify } from "node:util";
import { test } from "node:test";

const frontendRoot = path.resolve(import.meta.dirname, "..");
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
  assert.deepEqual(capability.permissions.filter((permission: string) => permission.startsWith("core:window:")), [
    "core:window:allow-close",
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
