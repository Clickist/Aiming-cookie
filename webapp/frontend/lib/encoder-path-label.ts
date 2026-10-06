/**
 * 设置页「性能」栏：自动录像编码路径 → 人话展示（纯展示映射，无副作用）。
 *
 * 取值清单来自 Rust 事实源：src-tauri/src/window_capture.rs 的
 * HardwareEncoderPath（#[serde(rename_all = "camelCase")]，序列化值即小驼峰
 * 变体名）。自动录像只会走三层里的硬件两档或末级软件回退；D3dFrameReadbackSinkWriter
 * 被 require_automatic_hardware 拒绝，不会出现在自动录像状态里。
 *
 * 合同（同 lib/capture-events.ts）：未知新枚举值原样透出，不编造解释、不崩。
 */

import { t, type MessageKey } from "./i18n/core";

/** 徽章语义色：ok=硬件（正常），warning=软件（警示），idle=中性（未在录制/未知）。 */
export type EncoderPathTone = "ok" | "warning" | "idle";

const ENCODER_PATH_LABELS: Record<string, { key: MessageKey; tone: EncoderPathTone }> = {
  mediaFoundationHardwareH264: { key: "settings.performance.encoderHardware", tone: "ok" },
  mediaFoundationHardwareAdapterLuidH264: { key: "settings.performance.encoderHardware", tone: "ok" },
  mediaFoundationSoftwareH264: { key: "settings.performance.encoderSoftware", tone: "warning" },
};

export interface EncoderPathDisplay {
  tone: EncoderPathTone;
  /** 未知枚举值时为 null，改用 raw 原文展示。 */
  key: MessageKey | null;
  /** 未知枚举值时为原始序列化值；其余为 null。 */
  raw: string | null;
}

/** encoderPath 为 null = 未在录制（含未启用/未起会话），按占位文案展示。 */
export function describeEncoderPath(encoderPath: string | null | undefined): EncoderPathDisplay {
  if (encoderPath == null) {
    return { tone: "idle", key: "settings.performance.notRecording", raw: null };
  }
  const label = ENCODER_PATH_LABELS[encoderPath];
  if (!label) return { tone: "idle", key: null, raw: encoderPath };
  return { tone: label.tone, key: label.key, raw: null };
}
