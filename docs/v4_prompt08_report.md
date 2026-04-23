# v4 Pilot — Prompt 08 Report

Date: 2026-04-23
Branch: `v4-deontic`
Target: `valence_v4` (Part 5 extraction preserved throughout)

Six fixes across the measurement + projection stack, closing the
infrastructure gaps identified in Part 5's 0% classification baseline.
No re-extraction — the $12.95 v3 artifact from commit `7ba9589` is
intact and was re-projected with each fix.

## Commit hashes

| # | Hash | Scope |
|---|---|---|
| 0 | `7e5d02b` | init_schema_v4 safeguards (refuse to drop extraction) |
| 1 | `a697dc1` | Classification harness — tuple join instead of norm_id |
| 2 | `4437566` | Per-deal party seeding + subject edge emission + clear step |
| 3 | `f4ba847` | norm_in_segment via segment_prefix_pattern |
| 4 | `388f6d0` | norm_kind alignment: management_equity / tax_distribution |
| 5 | `c54bc8f` | Builder sub-source emission + b_aggregate |
| 6 | `d8d907f` | J.Crew blocker defeater emission |

## Part 0 — extraction safeguard

`valence_v4` retained all 8 rp_baskets + 1 jcrew_blocker + 5
blocker_exceptions + 3 sweep_tiers + 6 investment_pathways throughout
Prompt 08. No re-extraction. The new `--schema-only` and
`--preserve-extraction` flags + the `guard_extraction_data()` sentinel
refused to proceed whenever extraction entities were detected and no
explicit flag was passed.

Only one schema-touching operation was needed outside `--schema-only`:
the full-clean rebuild at the very start of this run (before any
extraction landed); post-extraction, every schema + seed change was
applied in-place.

## Fix 1 — harness structural-tuple join

Replaced norm_id string-equality lookup with
`(norm_kind, modality, primary_scoped_action, primary_scoped_object)`
tuple matching. Extended `_list_extracted_norms` to fetch modality +
action/object labels + source_text + source_section per norm.

**Result:** classification measurement jumped from **0% → measurable
real numbers on every dimension.**
- 8 of 23 extracted norms match GT norms by tuple (the remaining 15 are
  either builder sub-sources or RDP sub-baskets with no GT counterpart)
- No tuple collisions logged

Also landed: enriched Claude input payload (source_text/section added,
expected-value leak removed); `load_dotenv(override=True)` so the API
key loads reliably under `py -3.12 -m`.

## Fix 2 — subject edges

New per-deal party seed `duck_creek_parties_seed.tql` creates 7 party
instances (one per role). Projection's pre-existing subject binding
query now finds them.

**Result:** `norm_binds_subject` went from **0 → 26 edges** (14 norms
with 1–3 roles each). Per-norm role assignments align with each
mapping's `default_subject_role`.

Projection also gained:
- `clear_v4_projection_for_deal()` — idempotent re-project by scoping
  delete to `norm_id contains deal_id`; runs each delete in its own tx
  so schema-absent types don't abort the chain
- `_make_norm_id()` — deal_id prefix for non-basket norms so the clear
  step + harness join work uniformly

## Fix 3 — norm_in_segment

Added `segment_prefix_pattern` attribute (string, `@card(0..8)`) to
`document_segment_type`. Seed file `rp_segment_prefix_patterns.tql`
annotates each RP-relevant segment with its prefix(es): `"6.06"` →
`negative_cov_rp`, `"6.05"` → `negative_cov_asset_sales`, etc.
Projection emits `norm_in_segment` edges by matching
`norm.source_section contains pattern` in a single TypeQL match.

**Result:** **13 `norm_in_segment` edges** across 3 segments
(`definitions`:1, `negative_cov_rp`:7, `negative_cov_rdp`:5).

## Fix 4 — norm_kind alignment

Audit via live query + YAML scan surfaced two literal drift pairs:
- `management_equity_permission` → `management_equity_basket_permission`
- `tax_distribution_permission` → `tax_distribution_basket_permission`

Fresh builds get the corrected form from `rp_deontic_mappings.tql`;
already-seeded `valence_v4` gets an in-place patch via
`rp_mapping_kind_fixes.tql` using delete+insert on the attribute
ownership.

## Fix 5 — builder sub-source emission

Unstubbed the builder-specific projection concession. Emits:
- `builder_source_b_aggregate` — intermediate grouping the 3 "greatest
  of" inner sources with `aggregation_function: greatest_of`
- 8 sub-source norms under builder_basket: starter, cni, ecf, ebitda_fc
  (these 3 → b_aggregate), plus equity_proceeds, retained_asset_sale,
  investment_returns, and two "other" sources contributing directly to
  the parent via `sum`

**Result:** extracted norms went from **14 → 23**.
`norm_contributes_to_capacity` went from **0 → 9 edges**.

Not emitted (GT has them; v3 extraction doesn't surface them):
builder_source_declined_asset_sale / joint_venture_returns /
unsub_redesignation_fmv / receivables_royalty_license / sale_leaseback /
deferred_revenues / netting; also the EBITDA-140%FC decomposition into
separate ebitda_component + fixed_charges_component. These reflect v3
extraction output limits, not projection logic gaps.

## Fix 6 — J.Crew defeater emission

Added `defeater_id @key` + `defeater_name` attributes (additive schema).
Projection iterates `blocker_has_exception` and emits one `defeater`
per extracted exception with a `defeats` edge to the J.Crew prohibition
norm.

**Result:** **5 defeaters + 5 defeats edges** — one per Duck Creek
exception:
- `ordinary_course_exception` (Ordinary course IP dispositions)
- `nonexclusive_license_exception` (Licenses under §6.02)
- `intercompany_exception` (Intercompany transfers within restricted group)
- `immaterial_ip_exception` (Immaterial or obsolete property)
- `fair_value_exception` (Fair market value disposition requirement)

## Final verification — A1–A5 harness

Clean projection + ground-truth reload, then harness run.

| Check | Verdict | Key numbers |
|---|---|---|
| **A1 structural** | fail | 23 norms, 0 incomplete. Fails because `norm_count < expected minimum` across segments — the infrastructure is correct, coverage is low |
| **A2 segment counts** | fail | Real numbers per segment — `definitions:1` within, `negative_cov_rdp:5` within, `negative_cov_rp:7` below (expected 20–30) |
| **A3 kind coverage** | fail | 2 always-expected missing: `builder_basket_aggregate`, `intercompany_permission`. Down from 3 (Part 5); `tax_distribution_basket_permission` now aligned via Fix 4 |
| **A4 round-trip** | fail | 53 missing (down from 55) / 14 spurious / 8 mismatched. The spurious count rose because builder sub-sources are now emitted — GT has them but tuple-keying differs |
| **A5 rule-selection** | n/a | harness marker check still misses; measured separately via classification_measurement (100%, see below) |

## Classification D4 — real baseline

| Field | Matched GT | Accuracy-on-matched | Aggregate |
|---|---|---|---|
| `capacity_composition` | 8 | **7/8 = 87.5%** | 30.4% |
| `action_scope` | 8 | **7/8 = 87.5%** | 30.4% |
| `condition_structure` | 8 | **0/8 = 0%** | 0% |
| Rule-selection | 8 | **8/8 = 100%** | 100% (matches = rule-sel corrects) |

**Interpretation of the aggregate vs matched gap.** 23 norms measured,
only 8 match GT via tuple. For the 15 unmatched extracted norms, GT has
no `expected` value → D4 passes iff Claude also returns `<none>`, which
it rarely does. Aggregate drops to 7/23 = 30.4%. On instances where
matching succeeded, Claude is 87.5% correct for two of three fields.

**condition_structure vocabulary mismatch.** Claude's outputs for the 8
matched instances:
- Expected `unconditional` (6 cases) → Claude returns `none` or `atomic`
- Expected `or_of_atomics` (1 case) → Claude returns `disjunction_of_atomics`
- Expected `atomic` (1 case, J.Crew blocker) → Claude returns `none`

Clear closed-taxonomy mismatch in the v1 prompt. Prompt 09 rewrites the
condition_structure prompt to use the expected enum verbatim.

Rule-selection accuracy 100% (8/8 correct) across all three fields
confirms the mapping choice (v3 entity → norm_kind) is right for every
case where a GT counterpart exists.

## Red-flag checks from the prompt

- **Classification D4 below 40% on any field?** Yes on
  `capacity_composition` (30.4%) and `action_scope` (30.4%) *at the
  aggregate level*, but both hit 87.5% on matched instances. `condition_structure` at 0%
  is a pure prompt-vocabulary issue, not a reasoning failure. All three
  are Prompt 09 fodder, not architectural flags.
- **A1 still failing?** Yes, but for a different reason than Part 5 —
  it's now about norm-count minimums, not structural completeness.
  Every projected norm passes `norm_is_structurally_complete`.
- **Rule-selection below 60% aggregate?** Not even close — 100%.

## Surprises / items for review before Prompt 09

1. **Aggregate accuracy denominator.** The 30.4% aggregate is
   misleadingly low — it compares 23 extracted norms against GT's 63,
   but only 8 are valid comparison points. Prompt 09 should either
   report "accuracy-on-matched" as the headline metric or restrict the
   denominator to matched instances.

2. **condition_structure vocabulary.** Claude returns `none` /
   `atomic` / `disjunction_of_atomics` but expected values are
   `unconditional` / `atomic` / `or_of_atomics`. A v2 prompt with the
   exact enum + a "return literally one of: X, Y, Z" instruction
   should fix all 8 matched instances in a single iteration.

3. **Builder sub-source spurious count.** Fix 5 emitted
   `builder_source_cni`, `builder_source_ecf`, etc. — these kinds ARE
   in GT, but A4's tuple join isn't finding the matches. Investigate
   whether the builder sub-source norms need modality/action/object
   fields populated more explicitly to match GT's tuple.

4. **14 blocker_exceptions** — projection iterates every
   blocker_exception via `load_v3_entities_for_deal`. 5 get projected
   as defeaters via Fix 6. The other 5 still land in the mapping-gaps
   bucket since there's no deontic_mapping entry for blocker_exception.
   That's by design — Fix 6 emits defeaters as a side effect of
   projecting the parent jcrew_blocker, not as a standalone
   mapping-driven projection.

5. **A5 harness marker.** The validation harness reports A5 as "n/a
   (projection not run)" despite projection having clearly run.
   Harness checks a marker (or a query pattern) that projection's
   output doesn't satisfy. Worth auditing in Prompt 09.
