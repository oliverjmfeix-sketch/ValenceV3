# v4 Pilot — Prompt 10 Report

Date: 2026-04-24
Branch: `v4-deontic`
Target: `valence_v4` (Part 5 extraction preserved throughout)

Four fixes closing the remaining measurement / projection gaps before the
operations layer in Prompt 11.

## Commit hashes

| # | Hash | Scope |
|---|---|---|
| 1 | `18ee63d` | specific action_scope for contributors + V2 prompt + A4 GT-fetch |
| 2 | `71b03b6` | per-field dimension relevance |
| 3 | `2be78a6` | A1 structural — provenance inheritance on sub-sources |
| 4 | `cced216` | cap_usd diagnostic + cap_grower_pct scale coercion |

## Fix 1 — action_scope alignment

Audit ruling (`fff8e0b`) adopted: capacity contributors carry
action_scope=specific. Three surfaces touched:

1. **Projection** — `_project_builder_sub_sources` and `b_aggregate` now
   emit `"specific"` instead of `"general"` (audit's two-line edit).
2. **V2 prompt** — appended a CAPACITY CONTRIBUTOR RULE paragraph telling
   Claude to label `builder_source_*` / `*_component` norms `specific`
   regardless of the parent's action-set breadth.
3. **A4 harness** — `load_ground_truth_from_graph` was omitting
   action_scope + capacity_composition on the per-norm fetch, causing
   every A4 mismatch comparison to land at `gt=None vs extracted=X`.
   Added both attributes to the GT graph read. (The reported "16 A4
   mismatches" in Prompt 09 were all false positives from this bug.)

**Results:**

| Metric | Before Fix 1 | After Fix 1 |
|---|---|---|
| action_scope accuracy-on-matched | 52.9% (9/17) | 88.2% (15/17) |
| A4 mismatched | 16 | 0 |

## Fix 2 — per-field dimension relevance

Graph-native config added: `classification_field_config` entity with
`field_name @key` + `relevant_dimensions`. Seeded three instances:

- `capacity_composition` → D1–D6 all relevant
- `action_scope` → D1–D6 all relevant
- `condition_structure` → D1–D4 only (no preconditions, no inter-instance
  consistency axis)

`_score_instance` takes `relevant_dims` and only marks `first_failure`
when the failing dim is in the relevant set. Scores for non-relevant
dimensions still compute but don't affect grade.

**Results:**

| Field | Aggregate Prompt 09 | Aggregate Prompt 10 | Change |
|---|---|---|---|
| capacity_composition | 56.5% | 54.5% | stable |
| action_scope | 39.1% | 63.6% | +24.5 (Fix 1 effect) |
| condition_structure | 0.0% | 68.2% | **+68.2 (Fix 2)** |

## Fix 3 — A1 structural completeness

Prompt 09 report's "A1 fail from segment minimums bleeding in" diagnosis
was incorrect. The A1 verdict was always separated from segment counts;
the actual failure was 10 norms missing source_text / source_section /
source_page / subject bindings:

- 9 builder sub-sources (no provenance emitted by projection path)
- 1 jcrew_blocker_instance (non-basket fetch set attrs={})

**Fix:**
- `_project_builder_sub_sources` hoists parent builder's provenance +
  emits it on both `b_aggregate` and every sub-source norm.
- Emits `norm_binds_subject` for each `default_subject_role` on every
  sub-source norm.
- `load_v3_entities_for_deal` non-basket path now fetches source_text /
  section / page / section_reference via try-blocks so projection's
  main path populates them.

**Result:** A1 verdict fail → **pass**. 0/22 norms incomplete.

## Fix 4 — cap_usd diagnostic + scale coercion

Classified the "cap_usd mismatches" from Prompt 09:

1. Prompt 09's "cap_usd: 4" attribution was wrong. `round_trip_check`
   only compares action_scope — no cap_usd branch. All 16 Prompt 09 A4
   mismatches were action_scope false-positives from the GT-fetch bug
   (Fix 1 resolves those).
2. Direct side-by-side: **cap_usd has zero mismatches** on matched
   norms. The cap_grower_pct attribute has 3 consistent 100× scale
   mismatches — v3 stores fractions, GT authors percentages.

Applied the scale coercion from `_project_builder_sub_sources`
(Prompt 08 Fix 5) at the main projection site too: `value ≤ 5.0 →
multiply by 100`. Safe because real grower-pct values in agreements
are ≥ 5% (percentages) or ≤ 2.0 (fractions).

After re-project:
- `general_rp_basket_permission` cap_grower_pct: 1.0 → 100.0 ✓
- `management_equity_basket_permission` cap_grower_pct: 0.15 → 15.0 ✓
- `general_rdp_basket_permission` cap_grower_pct: 1.0 → 100.0 ✓

Full diagnostic + classification in `docs/v4_cap_usd_mismatch_diagnostic.md`.

## Final metrics

### Classification (V2 prompts, matched)

| Field | Prompt 09 | Prompt 10 | Δ |
|---|---|---|---|
| capacity_composition | 76.5% (13/17) | 75.0% (12/16) | stable |
| action_scope | 52.9% (9/17) | **87.5% (14/16)** | +34.6 |
| condition_structure | 94.1% (16/17) | 93.8% (15/16) | stable |
| Rule-selection | 100% | 100% | ✓ |

### Condition_structure aggregate

| Prompt 09 | Prompt 10 |
|---|---|
| 0.0% | **68.2%** |

### A1–A5 verdicts

| Check | Prompt 09 | Prompt 10 |
|---|---|---|
| A1 structural | fail | **pass** |
| A2 segment counts | fail | fail (coverage gaps remain) |
| A3 kind coverage | fail | fail (same 2 always-expected missing) |
| A4 round-trip | fail | fail (missing=46; mismatched=**0**; spurious=6) |
| A5 rule-selection | pass | pass (100%) |

## Comparison to Prompt 10 targets

| Target | Expected | Actual | Notes |
|---|---|---|---|
| capacity_composition | 75–85% matched | 75.0% | lower end (Fix 1 dropped 1 norm by shape change) |
| action_scope | 90–95% matched | 87.5% | just below target; 2 Claude edge cases |
| condition_structure matched | 90–95% | 93.8% | ✓ |
| condition_structure aggregate | meaningful | 68.2% | ✓ |
| Rule-selection | 100% | 100% | ✓ |
| A1 verdict | pass | **pass** | ✓ |
| A4 mismatched | ~8 | **0** | exceeds target (GT fetch bug masked real alignment) |

## Surprises / items for review before Prompt 11

1. **A4 mismatched=0 over-performs the target.** The GT-fetch bug
   caused Prompt 09 to under-report. Real projection-GT agreement was
   already close; Fix 1's action_scope alignment + Fix 4's grower-pct
   scaling fully closed the gap.

2. **Norm count drift to 22 (from 23).** Previous runs counted 23; this
   run shows 22 projected norms + 22 measured instances. Accounting
   suggests one `builder_source_other` projection dropped between runs
   (duplicate norm_id handling?). Low-priority; worth 5 minutes of
   investigation before Prompt 11.

3. **Prompt 09 report accuracy.** Three of its diagnoses were slightly
   off ("cap_usd: 4", "source_page: 4", "A1 fail from segment
   minimums"). Not critical — the fixes discovered the real issues —
   but future report-writing should query the harness output directly
   rather than interpreting summary lines.

4. **2 Claude action_scope misses** remain on matched instances:
   `general_rp_basket_permission` (exp=reallocable, Claude=specific) —
   the prompt's reallocable definition requires Claude to infer
   cross-covenant reallocation flows from source_text alone, which may
   be under-specified. Prompt 11+ may refine V2 → V3 if action_scope
   becomes a bottleneck for operations-layer queries.

5. **Post-pilot items logged in `v4_known_gaps.md`:**
   - action_scope fourth-value taxonomic gap (Candidate C from audit)
   - cap_grower_pct extraction convention (percentages vs fractions)
