"""高手录像 CV 冒烟驱动脚本（实验性，未实跑验证）。

目的：验证 kovaak_tracker 免校准通用检测（generic_visual_detection）吃
"视频网站下载的高手 KovaaK 录像"时的目标检测覆盖率与计数合理性，
为离线高手基准提取工具立项提供数据。只读视频、只打统计，不写任何仓库文件。

用法（仓库根目录）：
    python scripts/pro_smoke.py <video.mp4> [--sample-every N] [--max-frames M]

参数：
    --sample-every N   每 N 帧处理一帧（默认 1＝全帧；先快速摸底可用 10）
    --max-frames M     最多处理 M 帧（默认不限）

产出：stdout 打印 JSON 统计块（复制回填到
docs/pro-benchmark-smoke-plan.md 的结果表）＋ 总耗时。

已知边界：本脚本写于 2026-08-27，调用的是当时 generic_visual_detection
的公开函数；若接口变动，报错信息直接带回仓库找 agent 修。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kovaak_tracker.generic_visual_detection import (  # noqa: E402
    detect_generic_targets,
    select_color_hypothesis,
)
from kovaak_tracker.video import get_video_metadata  # noqa: E402

HYPOTHESIS_SAMPLE_COUNT = 60   # 颜色假设选样帧数（视频开头均匀取）
HYPOTHESIS_SAMPLE_SPAN = 600   # 选样覆盖的前 N 帧


def main() -> int:
    parser = argparse.ArgumentParser(description="pro video CV smoke")
    parser.add_argument("video", help="path to downloaded pro KovaaK recording")
    parser.add_argument("--sample-every", type=int, default=1)
    parser.add_argument("--max-frames", type=int, default=0)
    args = parser.parse_args()

    video_path = Path(args.video)
    if not video_path.is_file():
        print(f"video not found: {video_path}", file=sys.stderr)
        return 2

    meta = get_video_metadata(video_path)
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        print("cannot open video", file=sys.stderr)
        return 2

    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 0.0

    # ── 1. 颜色假设选择（开头采样，fail-closed 直接报告） ──────────────
    step = max(1, HYPOTHESIS_SAMPLE_SPAN // HYPOTHESIS_SAMPLE_COUNT)
    sample_frames: list[np.ndarray] = []
    for idx in range(0, min(HYPOTHESIS_SAMPLE_SPAN, total_frames), step):
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, frame = cap.read()
        if ok and frame is not None:
            sample_frames.append(frame)
    hypothesis = select_color_hypothesis(sample_frames)
    if hypothesis is None:
        print(json.dumps({
            "video": video_path.name,
            "resolution": f"{int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))}x{int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))}",
            "fps": round(fps, 2),
            "total_frames": total_frames,
            "hypothesis": None,
            "fail_closed": True,
            "note": "颜色假设选择 fail-closed：该视频无法由免校准管线处理，记录来源与画面特征后带回",
        }, ensure_ascii=False, indent=2))
        cap.release()
        return 0

    # ── 2. 逐帧检测统计 ──────────────────────────────────────────────
    started = time.monotonic()
    processed = detected_frames = 0
    count_samples: list[int] = []
    shape_counter: Counter[str] = Counter()
    zero_runs: list[int] = []      # 连续 0 检出段长度
    current_zero_run = 0

    frame_idx = 0
    while True:
        ok, frame = cap.read()
        if not ok or frame is None:
            break
        if frame_idx % args.sample_every != 0:
            frame_idx += 1
            continue
        if args.max_frames and processed >= args.max_frames:
            break
        result = detect_generic_targets(frame, hypothesis)
        targets = result.get("targets", []) if isinstance(result, dict) else []
        processed += 1
        count_samples.append(len(targets))
        for target in targets:
            shape_counter[str(target.get("shape", "unknown"))] += 1
        if targets:
            detected_frames += 1
            if current_zero_run:
                zero_runs.append(current_zero_run)
                current_zero_run = 0
        else:
            current_zero_run += 1
        frame_idx += 1
    if current_zero_run:
        zero_runs.append(current_zero_run)
    elapsed = time.monotonic() - started
    cap.release()

    def q(sorted_vals: list[int], pct: float) -> int:
        if not sorted_vals:
            return 0
        return sorted_vals[min(len(sorted_vals) - 1, int(len(sorted_vals) * pct))]

    sorted_counts = sorted(count_samples)
    stats = {
        "video": video_path.name,
        "resolution": f"{sample_frames[0].shape[1]}x{sample_frames[0].shape[0]}" if sample_frames else "unknown",
        "fps": round(fps, 2),
        "total_frames": total_frames,
        "hypothesis": {
            "hsv_lower": [int(v) for v in np.asarray(hypothesis["hsv_lower"]).ravel()],
            "hsv_upper": [int(v) for v in np.asarray(hypothesis["hsv_upper"]).ravel()],
            "passes": True,
        },
        "processed_frames": processed,
        "sample_every": args.sample_every,
        "frame_coverage_pct": round(100.0 * detected_frames / processed, 1) if processed else 0.0,
        "targets_per_frame": {
            "min": sorted_counts[0] if sorted_counts else 0,
            "p50": q(sorted_counts, 0.50),
            "p90": q(sorted_counts, 0.90),
            "max": sorted_counts[-1] if sorted_counts else 0,
        },
        "shape_distribution": dict(shape_counter.most_common()),
        "zero_detection_runs": {
            "count": len(zero_runs),
            "longest": max(zero_runs) if zero_runs else 0,
        },
        "wall_seconds": round(elapsed, 1),
        "seconds_per_processed_frame_ms": round(1000.0 * elapsed / processed, 2) if processed else 0.0,
    }
    # 分辨率取自采样首帧（cap 已释放）
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
