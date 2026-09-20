# My aiming knowledge base (SDK template pack)

> English version of the template pack README ([template/README.md](../template/README.md)). This copy lives in `docs/` rather than inside `template/` because a pack directory may only contain the four whitelisted files (see [SPEC.en.md](../SPEC.en.md) §2); the template folder itself must stay importable as-is.

This is a **minimal valid knowledge pack that passes Aiming Cookie import validation as-is**. Copy the `template/` directory, replace the placeholder content with your own knowledge, and you have a distributable pack.

## What is in this pack

| File | Role | Do you need to change it |
|---|---|---|
| `manifest.json` | Pack identity: ID, name, author, version, compatibility declaration | **Required** (at minimum change `pack_id` and `author`) |
| `knowledge/registry.json` | Knowledge layer: long-form prose — explanations, mechanisms, cues, dosage, retest, stop rules, etc. | **Required** (replace the 3 example entries with your content) |
| `mapping.json` | Mapping layer (optional): rules for "which numbers count as which signal" | Optional: if you don't want rules, delete this file (and remove `ac_compat.mapping_schema` from the manifest to match) |
| `README.md` | This file: notes for whoever receives the pack | Recommended |

## The minimum four steps

1. **Change `pack_id`** (`manifest.json`): replace `com.example.my-kb` with your own reverse-domain-style ID (lowercase letters, digits, dots, hyphens), e.g. `com.coachwang.static-kb`. **Once published, never change the ID** — it is the user's installation key.
2. **Wire up the version binding**: in `knowledge/registry.json`, `registry_version` must be exactly `<pack_id>@<pack_version>`. Whenever you change `pack_version`, change it there too, or validation fails.
3. **Replace the example entries**: `knowledge/registry.json` contains three examples (explanation-only / experiment-capable / prescription). Write your entries following their field structure. The `signals` (e.g. `sparc low`) and `metric_refs` (e.g. `sparc`) in an entry **may only come from the official vocabulary**, found at `knowledge/mapping/vocabulary.v1.json`; put self-invented synonyms into `signal_aliases`.
4. **Validate locally**: from the Aiming Cookie repository root, run

   ```
   python -m kovaak_tracker.coach.knowledge_pack validate <your pack directory>
   ```

   Output starting with `OK ...` means the pack is valid; errors come with a code and a reason — look them up in §13 of [SPEC.en.md](../SPEC.en.md) to fix them.

## What each of the three example entries demonstrates

- `kb.my-kb.explanation.sparc` — `explanation_only`: the shallowest tier; provides explanations only, takes no part in diagnosis, and may not carry training fields such as cue/dosage.
- `kb.my-kb.experiment.sparc` — `candidate_experiment`: may take part in diagnosis and experiment suggestions, and therefore must carry `observation_refs`, `quality_prerequisites`, and `cue`/`dose_guardrail`/`matched_retest`/`stop_adjust_rule`.
- `prescription.sparc.smoothness` — a `prescription.*` prescription entry (highest tier): additionally carries `near_transfer_retest` and `scenario_prescription`. **Note**: `scenario_profile_ref` may only reference official reviewed scenarios (this example references `scenario:static.1wall_6targets_small@1`); you cannot invent scenario profiles.

## What to read next

- [SPEC.en.md](../SPEC.en.md) — the full format specification: field tables, the 6 narrowing rules, the mapping rule reference, and activation/fallback semantics. (中文版：[SPEC.md](../SPEC.md))
- [data-reference.en.md](data-reference.en.md) — the data reference: where each kind of data comes from, what it reflects, and how the analysis pipeline uses it.
- `knowledge/mapping/official.v1.json` (in the repository) — the official pack is the best reference; all 23 rules can be imitated one for one.
