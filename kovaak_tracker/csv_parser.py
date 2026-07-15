"""Parser for KovaaK's Stats CSV files.

A KovaaK's stats file is a single CSV with three sections separated by blank
lines (and a weapon-config line that precedes the summary):

  1. Kill table          — one row per kill, fixed 13-column header
  2. Weapon config row   — per-weapon aggregates, ends the kill table
  3. Summary block       — ``Key:,Value`` pairs (kills, TTK, Challenge Start...)
  4. Input config block  — ``Key:,Value`` pairs (FOV, DPI, Sens, Resolution...)

The kill table and the two key/value blocks are separated by blank lines.
``Challenge Start`` lives in the summary block and is the wall-clock anchor used
to convert ``Timestamp`` (also wall-clock ``HH:MM:SS.mmm``) into scenario-relative
seconds.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd

KILL_HEADER = (
    "Kill #",
    "Timestamp",
    "Bot",
    "Weapon",
    "TTK",
    "Shots",
    "Hits",
    "Accuracy",
    "Damage Done",
    "Damage Possible",
    "Efficiency",
    "Cheated",
    "OverShots",
)


# KovaaK's Sens Scale → game yaw(度/count)。
# cm/360 = 914.4 / (DPI × Horiz_Sens × yaw),其中 914.4 = 360 × 2.54。
# 信源:Gemini grounding search(2026-07-05)+ 社区共识(mouse-sensitivity.com / kovaaks.com)。
# Valorant yaw=0.07 经用户数据反推验证(DPI 1600, Horiz 0.16 → 51cm,接近实测 48)。
# 未列出的 game(如 R6 XFactorAiming)→ 返回 None,用户需手填 cm/360。
GAME_YAW: dict[str, float] = {
    "Source": 0.022,        # CS2 / CSGO / TF2
    "CSGO": 0.022,
    "CS2": 0.022,
    "Quake": 0.022,         # Quake Live / III / Reflex
    "Quake Live": 0.022,
    "Reflex": 0.022,
    "Apex Legends": 0.022,
    "Valorant": 0.07,       # Valorant 内部 yaw(非 CSGO 0.022)
    "Overwatch": 0.0066,
    "Overwatch 2": 0.0066,
    "Call of Duty": 0.0066,
    "COD": 0.0066,
    "Fortnite": 0.022,      # UE4 hipfire default;因 config 而异
}

# Case-insensitive lookup index: KovaaK's Sens Scale casing is not guaranteed,
# and cm_per_360 already lowercases for the "cm/360" literal check. This keeps
# GAME_YAW readable (proper game names) while making yaw lookups case-insensitive.
_GAME_YAW_LOWER: dict[str, float] = {k.lower(): v for k, v in GAME_YAW.items()}


@dataclass(frozen=True)
class KovaaKStats:
    """One parsed KovaaK's stats CSV file."""

    kills: pd.DataFrame
    summary: dict[str, str]
    config: dict[str, str]
    file_name: str

    @property
    def challenge_start(self) -> datetime:
        """Scenario start as wall-clock datetime (the alignment anchor)."""
        return _parse_wallclock(self.summary["Challenge Start"])

    @property
    def scenario(self) -> str:
        return self.summary["Scenario"]

    @property
    def dpi(self) -> int:
        return int(float(self.config["DPI"]))

    @property
    def fov(self) -> float:
        return float(self.config["FOV"])

    @property
    def horiz_sens(self) -> float:
        return float(self.config["Horiz Sens"])

    @property
    def vert_sens(self) -> float:
        return float(self.config["Vert Sens"])

    @property
    def resolution(self) -> str:
        return self.config["Resolution"]

    @property
    def sens_scale(self) -> str:
        """KovaaK's Sens Scale 字段(游戏名,决定 yaw 用于 cm/360 计算)。"""
        return self.config.get("Sens Scale", "")

    @property
    def yaw(self) -> float | None:
        """该 session 的 game yaw(度/count),基于 Sens Scale。未知 game → None。"""
        return _GAME_YAW_LOWER.get(self.sens_scale.lower())

    @property
    def cm_per_360(self) -> float | None:
        """从 DPI + Horiz Sens + yaw 算 cm/360。

        - Sens Scale = "cm/360" 时,Horiz Sens 直接是 cm/360 物理值(KovaaK's 特殊 scale)
        - 已知 yaw 的 game:cm/360 = 914.4 / (DPI × Horiz_Sens × yaw)
        - 未知 game / 缺 DPI / 缺 Horiz Sens → None(用户需手填)
        """
        scale = self.sens_scale
        if scale.lower() == "cm/360":
            try:
                return float(self.config["Horiz Sens"])
            except (KeyError, ValueError):
                return None
        yaw = _GAME_YAW_LOWER.get(scale.lower())
        if yaw is None:
            return None
        try:
            return round(914.4 / (self.dpi * self.horiz_sens * yaw), 2)
        except (KeyError, ValueError, TypeError, ZeroDivisionError):
            return None


def parse_stats_csv(csv_path: str | Path) -> KovaaKStats:
    """Parse a KovaaK's stats CSV into kills table + summary + config.

    Raises ``ValueError`` if the kill-table header does not match the expected
    schema (guards against unrelated CSVs being handed in).
    """
    csv_path = Path(csv_path)
    return parse_stats_bytes(csv_path.read_bytes(), file_name=csv_path.name)


def parse_stats_bytes(data: bytes, *, file_name: str = "<memory>") -> KovaaKStats:
    """Parse the exact Stats bytes already accepted by a source fingerprint."""
    try:
        lines = data.decode("utf-8-sig").splitlines()
    except UnicodeDecodeError as exc:
        raise ValueError("KovaaK Stats CSV is not valid UTF-8") from exc

    if not lines or lines[0].strip() != ",".join(KILL_HEADER):
        found = lines[0] if lines else "<empty>"
        raise ValueError(
            f"Unexpected KovaaK's stats header: {found!r}. "
            f"Expected {','.join(KILL_HEADER)}."
        )

    kill_rows: list[list[str]] = []
    weapon_row_idx = len(lines)
    for idx in range(1, len(lines)):
        line = lines[idx]
        if line.strip() == "":
            weapon_row_idx = idx
            break
        cells = line.split(",")
        # Kill table has exactly len(KILL_HEADER) columns. A line starting with
        # the weapon-name header ("Weapon,Shots,...") marks the weapon-config row
        # and ends the kill table.
        if cells and cells[0] == "Weapon":
            weapon_row_idx = idx
            break
        kill_rows.append(cells)
    summary, config = _parse_kv_blocks(lines, weapon_row_idx + 2)

    kills = pd.DataFrame(kill_rows, columns=list(KILL_HEADER))
    # Coerce numeric columns. TTK carries an "s" suffix ("0.395000s").
    kills["Kill #"] = pd.to_numeric(kills["Kill #"], errors="coerce").astype("Int64")
    for col in ("Shots", "Hits", "Cheated", "OverShots"):
        kills[col] = pd.to_numeric(kills[col], errors="coerce").astype("Int64")
    for col in ("Accuracy", "Damage Done", "Damage Possible", "Efficiency"):
        kills[col] = pd.to_numeric(kills[col], errors="coerce")
    kills["TTK"] = pd.to_numeric(kills["TTK"].str.rstrip("s"), errors="coerce")
    kills["Timestamp_dt"] = kills["Timestamp"].map(_parse_wallclock)
    kills["time_s"] = (kills["Timestamp_dt"] - _parse_wallclock(summary["Challenge Start"])).dt.total_seconds()

    return KovaaKStats(
        kills=kills,
        summary=summary,
        config=config,
        file_name=file_name,
    )


def _parse_kv_blocks(lines: list[str], start_idx: int) -> tuple[dict[str, str], dict[str, str]]:
    """Parse the two ``Key:,Value`` blocks after the weapon-config row.

    The first block is the challenge summary, the second (after a blank line) is
    the input/game config. A row is ``Key:,Value`` — i.e. the first cell is the
    key, the second cell is the value, the comma sits between them.
    """
    blocks: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for line in lines[start_idx + 1:]:
        if line.strip() == "":
            if current is not None:
                blocks.append(current)
                current = None
            continue
        if current is None:
            current = {}
        cells = line.split(",", 1)
        if len(cells) != 2:
            continue
        key, value = cells[0].strip(), cells[1].strip()
        if key.endswith(":"):
            key = key[:-1].strip()
        if key:
            current[key] = value
    if current is not None:
        blocks.append(current)

    summary = blocks[0] if blocks else {}
    config = blocks[1] if len(blocks) > 1 else {}
    return summary, config


def _parse_wallclock(ts: str) -> datetime:
    """Parse a KovaaK's wall-clock timestamp ``HH:MM:SS.mmm``.

    KovaaK's logs time-of-day with milliseconds and no date. We anchor to a
    fixed epoch date because only *deltas* between timestamps are used for
    alignment — the absolute date is irrelevant.
    """
    return datetime.strptime(ts, "%H:%M:%S.%f")


__all__ = ["KovaaKStats", "parse_stats_bytes", "parse_stats_csv", "KILL_HEADER"]
