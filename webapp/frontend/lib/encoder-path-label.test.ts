import assert from "node:assert/strict";
import { test } from "node:test";

import { describeEncoderPath } from "./encoder-path-label";
import { translate } from "./i18n/core";

// 性能栏编码路径映射合同：序列化值来自 window_capture.rs 的 HardwareEncoderPath
// （serde camelCase）。硬件两档 → 正常文案，软件回退 → 警示文案，null → 未在
// 录制占位；未知新枚举值原样透出，不编造解释、不崩。

test("both hardware encoder paths map to the same low-overhead label with ok tone", () => {
  for (const value of ["mediaFoundationHardwareH264", "mediaFoundationHardwareAdapterLuidH264"]) {
    const display = describeEncoderPath(value);
    assert.equal(display.tone, "ok");
    assert.equal(display.key, "settings.performance.encoderHardware");
    assert.equal(display.raw, null);
    assert.equal(translate("zh-CN", display.key), "硬件编码（省资源）");
    assert.notEqual(translate("en-US", display.key), display.key);
  }
});

test("software fallback maps to the warning label with warning tone", () => {
  const display = describeEncoderPath("mediaFoundationSoftwareH264");
  assert.equal(display.tone, "warning");
  assert.equal(display.key, "settings.performance.encoderSoftware");
  assert.equal(display.raw, null);
  assert.equal(translate("zh-CN", display.key), "软件编码（CPU 占用较高，游戏时可能卡顿）");
  assert.notEqual(translate("en-US", display.key), display.key);
});

test("null or undefined encoder path means not recording (placeholder, neutral tone)", () => {
  for (const value of [null, undefined]) {
    const display = describeEncoderPath(value);
    assert.equal(display.tone, "idle");
    assert.equal(display.key, "settings.performance.notRecording");
    assert.equal(display.raw, null);
    assert.equal(translate("zh-CN", display.key), "未在录制");
  }
});

test("unknown future enum value passes through raw, never crashes", () => {
  const display = describeEncoderPath("someFutureEncoderPath");
  assert.equal(display.tone, "idle");
  assert.equal(display.key, null);
  assert.equal(display.raw, "someFutureEncoderPath");
});
