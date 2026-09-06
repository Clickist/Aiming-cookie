import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { test } from "node:test";

// 加载慢专项（工作流 D）：设置页渐进渲染合同。
// 页面框架常驻、各分区局部 skeleton、请求一次并行发出、缓存先渲染后静默刷新。
const root = path.resolve(import.meta.dirname, "..");

async function source(relativePath: string): Promise<string> {
  return readFile(path.join(root, relativePath), "utf8");
}

test("Settings page frame stays mounted instead of a full-page loading return", async () => {
  const settings = await source("components/task6/SettingsWorkspace.tsx");
  // 白屏根源已删：loading 期间不再整页 return Loading。
  assert.doesNotMatch(settings, /if \(loading\) return/);
  // 错误页只允许在「刷新失败且没有任何可用内容」时出现。
  assert.match(settings, /loadError && !catalog && profiles\.length === 0/);
});

test("Settings shows per-section skeletons while each part is pending", async () => {
  const settings = await source("components/task6/SettingsWorkspace.tsx");
  const providerSection = await source("components/task6/ProviderSettingsSection.tsx");
  // Provider 分区：轻数据未到前局部 skeleton（主从式重做后 skeleton 随
  // 分区组件渲染，语义不变）。
  assert.match(providerSection, /\{loading \? \(\s*<Loading>正在读取设置<\/Loading>\s*\)/);
  // 采集 / 存储分区：重数据各自等待，不阻塞其它分区。
  assert.match(settings, /desktop && capture === null \? <Loading>正在读取采集状态<\/Loading> : null/);
  assert.match(settings, /desktop && storage === null \? <Loading>正在读取存储占用<\/Loading> : null/);
});

test("Settings issues light and heavy requests in one parallel batch", async () => {
  const settings = await source("components/task6/SettingsWorkspace.tsx");
  // 存储与未完成采集在轻数据 await 之前就发出（不再是两段瀑布）。
  assert.match(settings, /const heavyPromise = desktop\s*\?\s*Promise\.all\(\[getStorage\(\), listIncompleteCaptures\(\)\]\)/);
  // capture 3s 竞速先例保持不变。
  assert.match(settings, /Promise\.race\(\[getCaptureStatus\(\), captureTimeout\]\)/);
  // 轻数据与 runs 在同一个 Promise.all 中并行。
  assert.match(settings, /listProviderProfiles\(\),\s*\n\s*getProviderCatalog\(\)\.catch\(\(\) => null\),\s*\n\s*getCalibrationProfile\(\),\s*\n\s*desktop \? Promise\.race\(\[getCaptureStatus\(\), captureTimeout\]\) : Promise\.resolve\(null\),\s*\n\s*desktop \? listKovaakRuns\(\)/);
  // 重数据到货后独立上屏，不等轻数据完成。
  assert.match(settings, /setStorage\(nextStorage\);\s*\n\s*setIncomplete\(nextIncomplete\.items\);/);
});

test("Settings snapshot is stale-while-revalidate with a silent background refresh", async () => {
  const settings = await source("components/task6/SettingsWorkspace.tsx");
  assert.match(settings, /let settingsSnapshot: SettingsSnapshot \| null = null/);
  assert.match(settings, /if \(!force && settingsSnapshot\)/);
  // 缓存命中路径：先渲染、置 loading 为 false，再后台静默刷新。
  assert.match(settings, /applySnapshot\(settingsSnapshot\);\s*\n\s*setLoadError\(settingsSnapshot\.catalog === null\);\s*\n\s*setLoading\(false\);\s*\n\s*void refreshRemote\(\)\.catch/);
});
