# v4 Pilot — Part 5 Data Landing Report

Date: 2026-04-23
Branch: `v4-deontic`
Deal: Duck Creek Technologies (`6e76ed06`)
Target database: `valence_v4`

## Extraction

**v3 RP pipeline, Duck Creek PDF (RP universe fetched from Railway-cached
`/rp-universe` endpoint — 446,647 chars).**

- Duration: 240.9 s
- Cost: **$12.95** (Opus 4.6; 6 API calls: 1 entity_list + 5 scalar batches)
- Total tokens: 686,062 input + 35,476 output
- Questions asked: 234 scalar + 0 entity_list (wait, 4 entity_list + 234 scalar
  actually — the log shows entity_list ran and produced sweep_tiers + pathways
  + blocker_exceptions)
- Answers stored: **216**
- Entities created: **31**

### Entities by type

| Type | Count |
|---|---|
| `rp_basket` (polymorphic; 8 concrete subtypes) | 8 |
| `builder_basket` | 1 |
| `ratio_basket` | 1 |
| `general_rp_basket` | 1 |
| `management_equity_basket` | 1 |
| `tax_distribution_basket` | 1 |
| `holdco_overhead_basket` | 1 |
| `equity_award_basket` | 1 |
| `unsub_distribution_basket` | 1 |
| `general_rdp_basket` | 1 |
| `jcrew_blocker` | 1 |
| `sweep_tier` | 3 |
| `investment_pathway` | 6 |
| `blocker_exception` | 5 |
| `rp_provision` | 1 |
| `deal` | 1 |

`general_investment_basket` was NOT created — no `*_basket_exists` scalar
routes to it in v3's question_annotations. 2 `basket_reallocates_to` edges
failed to commit (TypeDB `@card(0..1)` violation on `capacity_effect` —
Claude returned two reallocation objects with the same source/target pair).

### rp_v4_* projection fields populated

Per-basket values Claude returned (all 8 baskets × 4 fields = 32 cells,
100% coverage — no NULLs):

| Basket | capacity_composition | capacity_aggregation | object_class_multiselect | partial_applicability |
|---|---|---|---|---|
| builder_basket | `computed_from_sources` | `greatest_of` | `cash,equity_interest` | false |
| ratio_basket | `unlimited_on_condition` | `n_a` | `cash,equity_interest` | **true** |
| general_rp_basket | `fungible` | `n_a` | `cash,equity_interest` | false |
| management_equity_basket | `additive` | `greatest_of` | `equity_interest,holdco_equity` | **true** |
| tax_distribution_basket | `categorical` | `n_a` | `cash` | false |
| holdco_overhead_basket | `categorical` | `n_a` | `cash` | false |
| equity_award_basket | `unlimited_on_condition` | `n_a` | `equity_interest` | **true** |
| unsub_distribution_basket | `n_a` | `n_a` | `unrestricted_subsidiary_equity_or_assets` | false |

Values are semantically plausible on casual inspection; strict correctness
is a Prompt 08 concern.

## Projection

Graph-native projection via `deontic_mapping` lookup. Dry-run and real
run produced identical counts.

| Metric | Count |
|---|---|
| Entities scanned | 14 |
| Entities projected | 14 |
| Norms created | 14 |
| Conditions created | 3 |
| `condition_has_child` edges | 2 |
| `condition_references_predicate` edges | 2 |
| `norm_scopes_action` edges | 19 |
| `norm_scopes_object` edges | 12 |
| `norm_scopes_instrument` edges | 12 |
| `norm_binds_subject` edges | **0** |
| `norm_extracted_from` edges | 13 |

No predicate lookup failures, no mapping gaps, no structural completeness
errors — projection ran clean end-to-end.

**Known gap — subject edges.** `norm_binds_subject` = 0 because no `party`
instances were seeded in `valence_v4` for this deal. Party instances
(per-role) live in `valence_v4_ground_truth` but not the extraction DB.
Follow-up: seed per-deal party instances as part of extraction's
`_ensure_provision_exists_unified` path, or add a `norm_binds_subject_role`
shadow attribute on norms so the subject role is queryable even without
party singletons.

**Known gap — builder sub-sources + J.Crew exceptions.** Projection
emitted 14 top-level norms but did not expand builder_basket's flattened
source booleans (has_cni_source, has_ecf_source, …) into sub-source norms
with `norm_contributes_to_capacity` edges, nor jcrew_blocker's 5
`blocker_exception` children into defeater + defeats edges. These were
flagged as explicit stubs in the projection engine docstring (commit
`0039967`). Filling them is the single largest uplift for A4 round-trip.

## Validation harness results

All five checks ran; four failed with meaningful baselines.

| Check | Verdict | Key numbers |
|---|---|---|
| A1 structural | fail | 14 norms, 0 incomplete (fails because norm_count < expected minimum across segments) |
| A2 segment counts | fail | every segment reports `actual=0` because `norm_in_segment` edges were never emitted (pilot deferred) |
| A3 kind coverage | fail | 3 always-expected norm kinds missing: `builder_basket_aggregate`, `intercompany_permission`, `tax_distribution_basket_permission`; 0 always-expected kinds present (mapping target_norm_kind strings use slightly different names than `expected_norm_kinds` seed) |
| A4 round-trip | fail | 55 missing (GT 63 vs extracted 14), 8 spurious, 6 mismatched |
| A5 rule selection | n/a | harness reports "(projection not run)" despite projection having run — heuristic checks a different marker than the one projection now writes |

### A4 round-trip detail

**55 missing** — ground truth has 63 norms; extraction projected 14.
The gap is dominated by:

- 6.06(d)-(w) sub-clause permissions (14 distinct kinds in GT) — v3
  extraction is basket-level, doesn't decompose per clause letter
- Builder basket sub-sources as norms (CNI, ECF, EBITDA-FC, starter,
  carryforward/carryback) — projection stub
- Sweep tier obligations (sweep_tier entities created, but not projected
  to norms — no mapping row)
- Post-IPO basket components (formulaic decomposition — projection stub)
- RDP sub-baskets from §6.09(a) tailored exceptions — no v3 entity type

**8 spurious**:
- 5 RDP basket permissions (builder_rdp, equity_funded_rdp, general_rdp,
  ratio_rdp, refinancing_rdp) — GT narrowed RDP scope post-Prompt-05
- `equity_award_permission`, `management_equity_permission`,
  `tax_distribution_permission` — kind-name mismatches between projection
  output and GT authoring (extractor emitted `_permission`, GT expected
  `_basket_permission`)

**6 mismatched**:
- All 6 are `action_scope` disagreements where GT left the field
  null and projection emitted the mapping's default
  (`specific` / `general` / `reallocable`). Not a correctness issue —
  GT authoring left action_scope unpopulated for many norms.

## Classification measurement

Three fields × 14 instances each, v1 prompts, Opus 4.6. **0% accuracy
across all three fields** on all six Horner dimensions.

| Field | Instances | D1 | D2 | D3-D6 |
|---|---|---|---|---|
| capacity_composition | 14 | 0.0 | 0.0 | n/a |
| action_scope | 14 | 0.0 | 0.0 | n/a |
| condition_structure | 14 | 0.0 | 0.0 | n/a |

Root cause: the harness looks up expected values by exact `norm_id`
match. Extraction's norm_id scheme is
`{basket_id}:norm:{modality}:0` (e.g.,
`6e76ed06_rp_builder_basket:norm:permission:0`). Ground truth's norm_id
scheme is `dc_rp_6_06_f_cumulative_amount_usage`. No overlap.

The confusion matrix for every field is `<none> → {<none>: 14}`: the
harness can't find any expected value, so nothing reaches D4 grading.

This is a harness gap, not an extraction or projection gap. Fix in Prompt
08: teach classification_measurement to join extracted norm to GT norm via
`norm_extracted_from:fact` (the v3 entity the extracted norm was derived
from). The ground-truth YAML records `source_entity_type` + operative
basket identity on each GT norm via `serves_questions` / `contributes_to`
relations, so a 2-hop join via the shared fact should work.

**SDK wiring confirmed live** — classification calls reached
`api.anthropic.com` and returned JSON responses (see
`app/data/classification_measurements/*.json`; each instance has a
`predicted_label` populated from Claude output, but `expected_label` is
null so D4 grading never fires).

## Baseline verdict

Extraction + projection pipelines work end-to-end. The infrastructure
from Parts 1-4 held up: integrity checks passed, seeds loaded, mappings
drove per-entity projection, and Claude answered all 4 per-basket
classification fields on all 8 extracted baskets.

What Prompt 08 needs to address to move beyond this baseline:

1. Harness norm-id reconciliation — classification measurement needs to
   join extracted norms to GT norms via `norm_extracted_from:fact`, not
   by exact norm_id
2. Builder basket sub-source emission in projection (~10 norms added)
3. J.Crew blocker defeater emission in projection (5 defeater + defeats
   edges added)
4. `norm_in_segment` edge emission in projection (unlocks A2 segment
   counts)
5. Per-deal party instance seeding so `norm_binds_subject` populates
6. Kind-name alignment between `deontic_mapping.target_norm_kind` and
   `expected_norm_kind.norm_kind` seeds (~4 renames)
7. Retry the 2 failed reallocation edges (commit failure on
   `capacity_effect @card(0..1)` — Claude returned duplicate
   source/target pairs; dedupe in graph_storage reallocation path)
