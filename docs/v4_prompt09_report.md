# v4 Pilot — Prompt 09 Report

Date: 2026-04-23
Branch: `v4-deontic`
Target: `valence_v4` (Part 5 extraction preserved throughout; no re-extraction)

Four fixes closing the measurement gaps from Prompt 08. All work
downstream of the $12.95 v3 artifact.

## Commit hashes

| # | Hash | Scope |
|---|---|---|
| 1 | `b182a9c` | V2 classification prompts (vocabulary alignment) |
| 2 | `a496308` | Builder sub-source tuple population (action/object edges) |
| 3 | `8b158ab` | A5 harness marker — real rule-selection accuracy |
| 4 | `ea50a9f` | Accuracy-on-matched as headline metric |

## Fix 1 — V2 classification prompts

V1 of `condition_structure` scored 0% on matched (Prompt 08). V1 vocabulary
(`none` / `disjunction_of_atomics` / `conjunction_of_atomics`) didn't match
GT's `unconditional` / `or_of_atomics` / `and_of_atomics`. V2 pins the enum
literally and embeds a translation table so Claude's first-instinct
vocabulary gets routed to the right value.

Prompt version system preserved: CLI `--prompt-version v1|v2` (default v2).
V1 prompts stay callable for comparison runs.

**Results (V1 → V2 on preserved extraction, before Fix 2 matching expansion):**
| Field | V1 matched | V2 matched |
|---|---|---|
| capacity_composition | 7/8 (87.5%) | 7/8 (87.5%) |
| action_scope | 7/8 (87.5%) | 7/8 (87.5%) |
| condition_structure | **0/8 (0%)** | **7/8 (87.5%)** |

Single condition_structure miss is tax_distribution (GT = `unconditional`,
Claude = `atomic` — Claude reading a qualifier as a predicate).

## Fix 2 — Builder sub-source tuple fix

**Diagnostic** — compared builder_source_* tuples in `valence_v4` (extracted)
vs `valence_v4_ground_truth`:

  - Extracted: 9 norms, `mod=permission`, `act=[]`, `obj=[]` ← **Case A**
  - GT: 18 norms, `mod=permission`, `act=[4 actions]`, `obj=[cash]`

Projection's `_project_builder_sub_sources` emitted norm entities but
skipped `norm_scopes_action` / `norm_scopes_object` edges. Fix: inherit
the parent builder's 4-way action scope (make_dividend_payment,
repurchase_equity, pay_subordinated_debt, make_investment) + `cash`
object on each sub-source and on the b_aggregate intermediate.

**A4 deltas:**
| Metric | Before Fix 2 | After Fix 2 |
|---|---|---|
| missing | 53 | 45 (−8) |
| spurious | 14 | 6 (−8; builder sub-sources no longer spurious) |
| mismatched | 8 | 16 (+8; more matches expose attribute diffs) |

Remaining 6 A4 spurious are all RDP basket permissions (GT narrows RDP
scope per Prompt 05) — expected, not a bug.

No case B/C/D surfaced; scope remained within "populate missing edges."

## Fix 3 — A5 harness marker

Prompt 08 reported A5 "n/a (projection not run)" despite projection
running. `check_rule_selection_accuracy` was a **pure stub** — returned
empty `per_entity_type` regardless of graph state.

Implementation uses the existing `norm_extracted_from:fact` edge (already
emitted by Prompt 07 projection). For each projected norm, trace back to
its v3 entity type, look up the deontic_mapping's expected
`target_norm_kind` for that type, compare to actual. No new entity type
needed.

**Result:**
- A5 verdict: **pass**
- Aggregate: **100.0% (23/23)**
- Per-entity-type: 13 types, each 1/1 correct
- 0 failures

## Fix 4 — Accuracy-on-matched as headline

Prompt 08's aggregate accuracy dilutes real signal: 15 of 23 extracted
norms had no GT counterpart via structural tuple, so they count against
the denominator but can't be scored. 7/23 = 30.4% looked bad; 7/8 = 87.5%
was the honest number.

Reshaped output: `headline_metric.accuracy_on_matched` leads, context
aggregate follows. `aggregate_accuracy` retained as back-compat alias for
consumers of the Prompt 08-era JSON shape.

CLI output format:
```
HEADLINE  accuracy-on-matched: 94.1%  (16/17 matched)
context   aggregate:           0.0%   (23 extracted, 6 unmatched)
```

## Final metrics

After all four fixes + full re-project + GT reload:

### Classification accuracy (v2 prompts)

| Field | Accuracy-on-matched | Matched count | Aggregate (context) |
|---|---|---|---|
| `capacity_composition` | **76.5% (13/17)** | 17 | 56.5% |
| `action_scope` | 52.9% (9/17) | 17 | 39.1% |
| `condition_structure` | **94.1% (16/17)** | 17 | 0.0% |
| Rule-selection | **100% (23/23 via A5)** | 23 | — |

Matched count jumped 8 → 17 from Prompt 08 because Fix 2's builder
sub-source edge population let those 9 sub-sources join GT. The
`action_scope` number dropped vs Prompt 08 (87.5%) because the new
builder sub-source matches introduced attribute disagreements (projection
emits `general` for sub-sources; GT authors `specific` for most).

### A1–A5 verdicts

| Check | Verdict | Notes |
|---|---|---|
| A1 structural | fail | 23/23 norms complete, but norm_count below segment minimums |
| A2 segment counts | fail | definitions:1 (within), rdp:5 (within), rp:7 (below expected 20–30) |
| A3 kind coverage | fail | 2 always-expected missing (`builder_basket_aggregate`, `intercompany_permission`) |
| A4 round-trip | fail | missing=45, spurious=6, mismatched=16 |
| **A5 rule-selection** | **pass** | **100% (23/23)** |

### Comparison vs Prompt 08 targets

| Target | Expected | Actual | Notes |
|---|---|---|---|
| capacity_composition | 85–95% on matched | 76.5% (13/17) | Lower end; builder sub-sources pull it down |
| action_scope | 85–95% on matched | 52.9% (9/17) | Below expected; builder sub-source action_scope mismatch |
| condition_structure | 75–100% on matched | **94.1% (16/17)** | On target |
| Rule-selection | 100% | **100%** | ✓ |
| A5 reports | real 100% | **100%** | ✓ |
| A4 spurious | ~6–8 | **6** | ✓ |
| A4 missing | ~45–48 | **45** | ✓ |
| Aggregate framing | matched as headline | done | ✓ |

### Red-flag review

- `condition_structure` stays below 50% on matched after V2? **No — 94.1%.**
- Builder sub-source diagnostic reveals case C structural issue? **No — Case A (pure edge emission gap), fix was mechanical.**
- A5 requires schema changes beyond attribute tweaks? **No — existing `norm_extracted_from` edge is the signal.**

## Surprises / items for review before Prompt 10

1. **action_scope dropped from 87.5% → 52.9% on matched.** Fix 2 added
   9 builder sub-source matches, but projection emits `action_scope=general`
   for them while GT authors `specific` for most. Either the GT YAML should
   be audited (is `specific` the right call for an internal builder
   contribution?) or projection should default to `specific` for
   sub-sources. Worth discussing before iterating the prompt.

2. **`condition_structure` aggregate shows 0%** despite 94.1% headline.
   The grade system requires passing D1 through D6 (including D5/D6 which
   are `n/a` for condition_structure). Grade never reaches "pass," so the
   aggregate = 0. Not misleading (headline is clear) but worth reconciling —
   either fix the grade computation for fields with only D1-D4 relevance,
   or deprecate the aggregate row entirely for condition_structure.

3. **A4 mismatched rose 8 → 16** when matching more norms. Each new match
   surfaces additional attribute disagreements that the old denominator
   hid. Inspecting the 16 mismatches shows patterns: action_scope (8 of
   16), cap_usd (4), source_page (4). A future pass can classify whether
   these are projection emission gaps vs GT authoring decisions.

4. **A1 still fails despite 23/23 structurally complete.** The fail
   verdict is keyed on segment-level minimum norm counts (A2's concern
   bleeding into A1's summary), not on structural completeness itself.
   Worth refactoring the harness to report A1 purely on
   `norm_is_structurally_complete` results.

5. **Builder sub-source count in valence_v4 is 9, GT is 18.** Projection
   covers the subset v3 extraction surfaces (starter, CNI, ECF, EBITDA-FC,
   b_aggregate, plus equity/asset/investment/debt catch-alls). GT has 9
   more (declined_asset_sale, joint_venture_returns, unsub_redesignation_fmv,
   receivables_royalty_license, sale_leaseback, deferred_revenues, netting,
   ebitda_component, fixed_charges_component). These require extraction
   pipeline additions — Prompt 10+ scope.
