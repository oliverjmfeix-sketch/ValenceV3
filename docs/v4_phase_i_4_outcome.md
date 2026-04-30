# Phase I.4 outcome — synthesis_guidance v4 rewrite (eval-priority categories)

> Phase I.4 rewrites three eval-priority categories' `synthesis_guidance`
> to v4 vocab and adds an L-specific `stage1_picker_guidance`. Two
> commits on `v4-deontic`.

## Summary

| Workstream | Outcome |
|---|---|
| Implementation (data only; idempotent applier) | ✓ commit `5cd9dd6` |
| Validation harness | ✓ baseline preserved (A4 m=42 s=6 mm=0) |
| Lawyer eval re-run | 6/6 OK; cost $0.8271; latency 317s |
| Q3 framing | ↑ marginally (cross-covenant reallocability stated) |
| Q4 carveout naming | ✗ unchanged (Stage 1 still classifies SUPPLEMENTARY) |
| Q5 RDP floor sum | ✗ unchanged ($260M floor; RDP cited but not summed) |
| Stop-loss | ✓ no PASS→PARTIAL/FAIL regression |

Total cost: $0.8271 (eval). Cumulative Phase I: $3.2192.

## Implementation

`app/data/seed_synthesis_guidance.tql`:
- **Category L** (Asset Sale Proceeds & Sweeps): 374 chars → 3493 chars.
  Leads with v4 governance norms (sweep_tier_*, unlimited_asset_sale_basket_permission,
  sweep_exemption_product_line, sweep_exemption_de_minimis,
  builder_source_retained_asset_sale_proceeds). Each named with norm_id +
  section + load-bearing role. v3 attrs retained as fallback.
- **Category N** (Dividend Capacity): 745 chars → 4209 chars (+ 3464).
  Appended REALLOCABLE-RDP CONTRIBUTION TO DIVIDEND FLOOR section
  with worked example: $130M (general_rp) + $130M (general_rdp,
  reallocable, no shared-pool link) + $130M (builder_starter) = $390M
  minimum floor. Targets Q5's persistent $260M floor miss.
- **Category I** (Reallocation): 510 chars → 2383 chars. Full v4-vocab
  rewrite. Reasons over `action_scope` and `shares_capacity_pool`
  (replaces v3 `basket_reallocation` entity refs which don't exist
  in v4 graph).

`app/data/seed_stage1_picker_guidance.tql`:
- **Category L** (NEW): 0 → 1588 chars. Instructs Stage 1 to mark
  sweep_tier + carveout norms as PRIMARY for asset-sale questions
  even when scoped_actions=[make_investment].

`app/scripts/phase_i4_apply_guidance.py` (NEW): idempotent applier
loading from seed files and upserting. Uses `load_dotenv(override=False)`
so the caller's `TYPEDB_DATABASE` env is honored.

### Mid-flight v3 cleanup (incident report)

The first apply attempt used `load_dotenv(override=True)` which
silently overrode `TYPEDB_DATABASE=valence_v4` to `valence` (the v3
production DB) per the main `.env` file. The four upserts hit `valence`
instead of `valence_v4`. Detected immediately when the script log
showed "Target DB: valence". Rolled back via a temp script that:

1. Restored `valence` L/N/I synthesis_guidance to main-branch
   canonical values (374 / 745 / 510 chars).
2. Deleted the erroneously-added L stage1_picker_guidance attribute
   (main has no L picker).
3. Re-ran the upsert against the correct `valence_v4` target.

The applier was hardened to `override=False` to prevent recurrence.
No data loss; v3 (production) ended up at canonical state. v4 received
the I.4 changes as intended. Validation harness (A1/A4/A5/A6)
preserved across both writes.

## Validation harness baseline (post-I.4)

A1=pass, A2/A3 fail (pre-existing), A4 m=42 s=6 mm=0 (preserved
from I.2/I.3), A5=pass aggregate_accuracy=1.0, A6=pass.

## Lawyer eval per-question results

```
duck_creek_q1 (builder):        primary  9→10  cite  9→10  PASS preserved
duck_creek_q2 (unsub):          primary  1→ 1  cite  4→ 3  PASS preserved
duck_creek_q3 (reallocation):   primary  4→ 4  cite  4→ 4  PARTIAL (richer framing)
duck_creek_q4 (asset_sale):     primary  2→ 2  cite  5→ 5  PARTIAL (unchanged)
duck_creek_q5 (total_capacity): primary 11→11  cite 11→11  PARTIAL (unchanged)
duck_creek_q6 (ratio):          primary  1→ 1  cite  4→ 3  PASS preserved
```

Conformance (must_cite ⊆ citations): **6/6** preserved.

### Q3 — marginal framing improvement

Cross-covenant reallocability framing strengthened. The post-I.4
answer states "the general RP basket and the general RDP basket share
a single fungible pool capped at the greater of $130,000,000 and
100% of Consolidated EBITDA — capacity not consumed by subordinated
debt prepayments is available for dividends/restricted payments and
vice versa." Improved over I.3's framing (which said "reallocable …
indicating fungibility at the same dollar cap") by being more
specific.

Tailored carveouts (intercompany / reorganization / IPO RDP) still
not enumerated — extraction-blocked.

### Q4 — picker guidance ineffective

Stage 1 still classifies sweep_tier and carveout norms as
SUPPLEMENTARY despite explicit picker-guidance instructions to mark
them PRIMARY. Rationale strings reveal Stage 1's reasoning:

- sweep_tier_100pct: "Governs mandatory sweep of asset sale proceeds,
  context for retained proceeds."
- unlimited_asset_sale_basket_permission: "Asset sale permission norm,
  not about using proceeds for dividends."
- sweep_exemption_product_line: "Sweep exemption for product line
  sales; contextually relevant to proceeds retention."

Stage 1's classifier reasons over the question's literal phrasing
("asset sale proceeds for dividends") and applies its own judgment
that these norms are about asset-sale-permission rather than
dividend-permission. The picker guidance — even with explicit "DO
NOT classify these as SUPPLEMENTARY on the basis that scoped_actions
excludes 'make_dividend_payment'" — does not override that judgment.

This is **Phase G's "graded authority hierarchy"** finding
empirically demonstrated. Stage 1 picker guidance is a hint, not a
hard constraint. The architecture has limits on how much LLM
classification can be steered via natural-language data.

The Q4 answer text mentions "Section 2.10(c)" generally but not
"Section 2.10(c)(iv)" specifically. 6.05(z) not surfaced.

### Q5 — N supplement ineffective

Stage 2 still concludes "two independent general_purpose pools with
a combined fixed-dollar floor of $260M" despite the N
synthesis_guidance now containing an explicit worked example
showing $390M ($130M RP + $130M RDP + $130M builder_starter).

The must-cite layer forces general_rdp_basket_permission into
citations; the relevance scoring places it at score 0.37; the N
guidance instructs sum semantics. None individually reach the
threshold to override Stage 2's interpretation that RDP "supplements"
rather than "adds to" the dividend floor.

### Q1, Q2, Q6 — PASS preserved

- Q1: cite +1. Builder constellation slightly more complete.
- Q2: cite -1. Lost one supplementary; answer remains correct.
- Q6: cite -1. Lost one supplementary; answer remains correct.

## What I.4 changed (and didn't change) in the architecture

**Changed:**
- L synthesis_guidance now leads with v4 norm names (sweep_tier_*,
  unlimited_asset_sale_basket_permission, sweep_exemption_product_line)
  with explicit instruction to cite them. Future Q4 work has the
  guidance substrate ready.
- N synthesis_guidance contains the canonical $390M worked example.
  Documenting the SSoT calculation independent of whether this
  particular Stage 2 run obeys it.
- I synthesis_guidance is v4-vocab — references action_scope and
  shares_capacity_pool. Removes confusing v3 entity refs that don't
  exist in v4 graph.
- L stage1_picker_guidance authored. The picker guidance pattern is
  available for future categories.
- Phase I.4's idempotent applier (`phase_i4_apply_guidance.py`) is
  the canonical pattern for future per-category guidance updates.

**Empirically didn't change:**
- Stage 1 classification verdicts (Q4 still classifies carveouts
  as SUPPLEMENTARY).
- Stage 2 dividend floor for Q5 (still $260M; RDP cited but not
  summed).

The graph state (norms + edges) is correct; the picker guidance is
correct; the synthesis_guidance is correct. The remaining gap is at
the LLM-classification and LLM-synthesis boundary — fundamentally
limited by how strictly an LLM follows natural-language data
guidance against its own pretrained interpretations of question
intent.

## What I.4 deferred

- **Q4 carveout naming as PRIMARY citations.** Picker guidance
  doesn't reach this. Future work could:
  - Boost relevance score for governance norms when category L is
    matched (re-tune I.3 weights).
  - Add a Stage 1-level "must-pick-as-primary" mechanism (analogous
    to the must-cite layer added in I.1 for Stage 2). This would
    require a synthesis_v4.py change to read a per-category
    must-pick list from the picker guidance and inject it as a
    structured payload key.
  - Use a more capable model for Stage 1 (the current default is
    claude-sonnet-4-6).

- **Q5 RDP floor sum.** Synthesis_guidance updates don't change
  Stage 2's interpretation. Same options apply.

- **F / G / M / other category lock-in rewrites.** PASS questions
  on stable v3 vocab; deferred per "stop reinventing the wheel"
  guidance. Can be picked up if future eval expansion shows
  vocab drift causing misses.

## Branch state at I.4 end

- Branch: `v4-deontic`
- HEAD: this commit (Phase I commit 8)
- Commits ahead of `origin/v4-deontic`: 8
- Push deferred to end of Phase I.

Phase I.4 complete. Moving to Phase I.5 (RP extraction-prompt fixes
— Q3 carveout questions/schema authored additively, rp_l25 prompt
iteration for product_line_2_10_c_iv_threshold null).
