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


def parse_stats_csv(csv_path: str | Path) -> KovaaKStats:
    """Parse a KovaaK's stats CSV into kills table + summary + config.

    Raises ``ValueError`` if the kill-table header does not match the expected
    schema (guards against unrelated CSVs being handed in).
    """
    csv_path = Path(csv_path)
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        lines = f.read().splitlines()

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
    kills["TTK"] = kills["TTK"].str.rstrip("s").astype(float)
    kills["Timestamp_dt"] = kills["Timestamp"].map(_parse_wallclock)
    kills["time_s"] = (kills["Timestamp_dt"] - _parse_wallclock(summary["Challenge Start"])).dt.total_seconds()

    return KovaaKStats(
        kills=kills,
        summary=summary,
        config=config,
        file_name=csv_path.name,
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


__all__ = ["KovaaKStats", "parse_stats_csv", "KILL_HEADER"]
