import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { test } from "node:test";

const frontendRoot = path.resolve(import.meta.dirname, "..");

async function source(relativePath: string): Promise<string> {
  return readFile(path.join(frontendRoot, relativePath), "utf8");
}

test("Tauri embeds the static export and enables the NSIS bundle", async () => {
  const config = JSON.parse(await source("src-tauri/tauri.conf.json"));
  assert.equal(config.build.frontendDist, "../out");
  assert.equal(config.bundle.active, true);
  assert.deepEqual(config.bundle.targets, ["nsis"]);
});

test("Next production build is a static export", async () => {
  const config = await source("next.config.ts");
  assert.match(config, /output:\s*"export"/);
  assert.match(config, /images:\s*\{\s*unoptimized:\s*true/);
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
