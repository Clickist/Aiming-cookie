/**
 * Client-side parser for KovaaK's Stats CSV — extracts config block fields
 * (FOV / DPI / Horiz Sens / Resolution) so the upload form can auto-fill.
 *
 * Server-side full parser is kovaak_tracker/csv_parser.py (also extracts kills
 * table + summary). This is a minimal TS mirror for the **config block** only
 * (the last Key:,Value block in the file) — enough to pre-fill the form.
 *
 * KovaaK CSV layout (sections separated by blank lines):
 *   1. Kill table (starts with "Kill #,Timestamp,...")
 *   2. Weapon config row
 *   3. Summary block — Key:,Value pairs (Scenario / Challenge Start / ...)
 *   4. Input config block — Key:,Value pairs (FOV / DPI / Horiz Sens / ...)
 * We want block 4. It's the last Key:,Value group, so walk backwards.
 *
 * Note: cm/360 is NOT in the CSV (only DPI + Sens components). Computing cm/360
 * from DPI × Sens gives wrong values for KovaaK (game-specific sens scale like
 * Valorant), so we don't auto-fill cm/360 — user input remains authoritative.
 */

export interface KovaaKConfigExtract {
  fov?: number;
  dpi?: number;
  horizSens?: number;
  vertSens?: number;
  resolution?: string;
  /** All parsed Key→Value pairs from the config block (for debugging / display). */
  raw: Record<string, string>;
}

export function parseKovaaKConfig(text: string): KovaaKConfigExtract {
  const lines = text.split(/\r?\n/);
  const raw: Record<string, string> = {};
  let foundAny = false;

  for (let i = lines.length - 1; i >= 0; i--) {
    const line = lines[i].trim();
    if (!line) {
      // Blank line — if we've started collecting, this is the block boundary.
      if (foundAny) break;
      continue;
    }
    // KovaaK Key:,Value rows: "FOV:,103.0" → key "FOV", value "103.0".
    const m = line.match(/^([^,]+?):\s*,\s*(.*)$/);
    if (!m) {
      // Non-Key:,Value line (e.g. kill table row) — block boundary.
      if (foundAny) break;
      continue;
    }
    raw[m[1].trim()] = m[2].trim();
    foundAny = true;
  }

  const num = (k: string): number | undefined => {
    const v = raw[k];
    if (v === undefined) return undefined;
    const n = Number(v);
    return Number.isFinite(n) ? n : undefined;
  };

  return {
    fov: num("FOV"),
    dpi: num("DPI"),
    horizSens: num("Horiz Sens"),
    vertSens: num("Vert Sens"),
    resolution: raw["Resolution"],
    raw,
  };
}
