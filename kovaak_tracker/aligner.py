"""Align KovaaK's CSV kill events with CV-tracked video trajectory.

The CSV gives scenario-relative timestamps (``time_s`` = seconds since the
scenario's ``Challenge Start``). The CV tracking data (``calibration_raw.csv``
written by :mod:`kovaak_tracker.tracking`) gives per-frame ball/crosshair
positions indexed by original video frame number, with its own ``time_s``
measured from the *first processed frame* of the trimmed clip.

These two clocks are the same scenario, but offset by however much video the
user recorded before the scenario actually started (menu navigation, countdown,
etc.). The user pins the offset by choosing ``start_frame`` — the original video
frame index at which the scenario began (CSV ``time_s == 0``).

Once pinned:

    csv_relative_time + (start_frame / fps)  ==  video_time_s

so a kill at CSV ``time_s = t`` lands on original frame
``start_frame + t * fps`` and its ball position is interpolated from the
CV track at that frame.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .csv_parser import KovaaKStats


@dataclass(frozen=True)
class AlignedKill:
    """One CSV kill event enriched with video-frame + ball-position data.

    ``first_shot_frame`` / ``kill_frame`` are original video frame indices.
    ``ball_pos`` is the interpolated (x, y) of the tracked target at kill time.
    For single-shot kills ``first_shot_frame == kill_frame``. For Shots>=2 the
    first shot (the miss) is placed at ``kill_frame - ttk * fps``.
    """

    kill_num: int
    time_s: float
    ttk: float
    shots: int
    hits: int
    kill_frame: int
    first_shot_frame: int
    ball_pos: tuple[float, float]
    in_track: bool


@dataclass(frozen=True)
class Alignment:
    """Result of aligning a CSV with a CV trajectory."""

    start_frame: int
    fps: float
    kills: list[AlignedKill]
    track_df: pd.DataFrame
    stats: KovaaKStats

    @property
    def aligned_kills(self) -> pd.DataFrame:
        rows = [
            {
                "Kill #": k.kill_num,
                "time_s": k.time_s,
                "TTK": k.ttk,
                "Shots": k.shots,
                "kill_frame": k.kill_frame,
                "first_shot_frame": k.first_shot_frame,
                "ball_x": k.ball_pos[0],
                "ball_y": k.ball_pos[1],
                "in_track": k.in_track,
            }
            for k in self.kills
        ]
        return pd.DataFrame(rows)


def align(
    stats: KovaaKStats,
    track_df: pd.DataFrame,
    fps: float,
    start_frame: int,
) -> Alignment:
    """Align CSV kills against the CV trajectory at ``start_frame``.

    ``track_df`` is the CV output (``calibration_raw.csv``) with columns
    ``frame, time_s, ball_x, ball_y`` at minimum. ``start_frame`` is the original
    video frame at which the scenario began.
    """
    if fps <= 0:
        raise ValueError(f"fps must be positive, got {fps}")
    if "frame" not in track_df or "ball_x" not in track_df or "ball_y" not in track_df:
        raise ValueError("track_df must have columns: frame, ball_x, ball_y")
    track_sorted = track_df.sort_values("frame").reset_index(drop=True)
    track_frames = track_sorted["frame"].to_numpy(dtype=float)
    track_x = track_sorted["ball_x"].to_numpy(dtype=float)
    track_y = track_sorted["ball_y"].to_numpy(dtype=float)
    track_lo = float(track_frames.min())
    track_hi = float(track_frames.max())

    # Rename columns that aren't valid identifiers (e.g. "Kill #") so itertuples
    # exposes them by a stable attribute name instead of positional (_0, _1...).
    kills_view = stats.kills.rename(columns={"Kill #": "kill_num"})
    aligned: list[AlignedKill] = []
    for row in kills_view.itertuples(index=False):
        kill_frame = start_frame + row.time_s * fps
        kill_frame_i = int(round(kill_frame))
        # First shot (the miss, for Shots>=2) sits TTK before the kill.
        first_shot_frame_i = int(round(start_frame + (row.time_s - row.TTK) * fps))
        in_track = track_lo <= kill_frame <= track_hi
        if in_track:
            bx = float(np.interp(kill_frame, track_frames, track_x))
            by = float(np.interp(kill_frame, track_frames, track_y))
        else:
            bx, by = float("nan"), float("nan")
        aligned.append(
            AlignedKill(
                kill_num=int(row.kill_num),
                time_s=float(row.time_s),
                ttk=float(row.TTK),
                shots=int(row.Shots),
                hits=int(row.Hits),
                kill_frame=kill_frame_i,
                first_shot_frame=first_shot_frame_i,
                ball_pos=(bx, by),
                in_track=in_track,
            )
        )

    return Alignment(
        start_frame=start_frame,
        fps=fps,
        kills=aligned,
        track_df=track_sorted,
        stats=stats,
    )


def coverage_report(alignment: Alignment) -> dict[str, int | float]:
    """How many kills fall inside the tracked range — the analyzable sample."""
    total = len(alignment.kills)
    in_track = sum(1 for k in alignment.kills if k.in_track)
    multi_shot = sum(1 for k in alignment.kills if k.shots > 1 and k.in_track)
    return {
        "total_kills": total,
        "kills_in_track": in_track,
        "coverage_pct": round(100.0 * in_track / total, 1) if total else 0.0,
        "multi_shot_in_track": multi_shot,
    }


__all__ = ["AlignedKill", "Alignment", "align", "coverage_report"]
