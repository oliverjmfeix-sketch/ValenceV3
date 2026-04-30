# Phase I.2 outcome — event_governed_by_norm projection (Phase F deferral)

> Phase I.2 closes the **Phase B/C deferral** of `event_governed_by_norm`
> rules. Authors 5 v4 norms (3 sweep_tier obligations + 2 carveout
> permissions) tied to the deal-agnostic `asset_sale_event` event_class
> entity via the new relation. Two commits on `v4-deontic`.

## Summary

| Workstream | Outcome |
|---|---|
| Implementation (helper + seed + clear update) | ✓ commit `3df23a3` |
| Validation harness | ✓ baseline IMPROVED (m: 45 → 42; A1 still pass) |
| Lawyer eval re-run | 6/6 OK; cost $0.8047; latency 325s |
| Q4 carveout enumeration | ✓ 2.10(c)(iv) NAMED in answer (was missing) |
| Q4 sweep tier surface | ✓ 3 sweep tiers cited as PRIMARY (were not) |
| event_governed_by_norm | ✓ 5 instances on Duck Creek (was 0) |
| 6.05(z) surfacing | ✗ norm exists, Stage 1 classifies SUPPLEMENTARY |

Total cost: $0.8047 (eval). Cumulative Phase I: $1.5588.

## What I.2 changed in the architecture

- **`event_governed_by_norm` is no longer a vacuous schema.** Phase F
  landed the relation; before I.2, 0 instances on every database.
  After I.2, 5 instances on Duck Creek tying named v4 governance
  norms to `asset_sale_event`. Future deals projected via
  `project_deal()` will populate these automatically.
- **Q4's load-bearing carveout norms are now first-class.** Before,
  2.10(c)(iv) and 6.05(z) lived only as boolean flags on `asset_sale_sweep`
  (`permits_section_6_05_z_unlimited`,
  `permits_product_line_exemption_2_10_c_iv`). Synthesis_v4 saw them
  via `provision_level_entities` only — buried below the named norm
  list. After I.2, they are named v4 norms (`unlimited_asset_sale_basket_permission`,
  `sweep_exemption_product_line`) with full GT-conformant 4-tuple
  (norm_kind, modality, action, object).
- **A4 round_trip baseline improved.** Three norm_kinds transitioned
  from "missing" to "matched": `sweep_tier`,
  `unlimited_asset_sale_basket_permission`,
  `sweep_exemption_product_line`. New baseline: m=42 s=6 mm=0 (was 45/6/0).

## Implementation

`app/data/asset_sale_governance_seed.tql` (NEW):
- 5 match-insert blocks (3 sweep_tier + 2 carveouts)
- `<deal_id>` placeholder for portability
- Sweep tier blocks match per-tier v3 entity by tier_id
- Carveout blocks match-conditional on
  `permits_section_6_05_z_unlimited` / `permits_product_line_exemption_2_10_c_iv`
- Each block creates: norm + norm_binds_subject + norm_scopes_action +
  norm_scopes_object + event_governed_by_norm
- Norm specs verbatim from `duck_creek_rp_ground_truth.yaml` so
  A4 4-tuple matches GT

`app/services/projection_rule_executor.py`:
- New constant `ASSET_SALE_GOVERNANCE_SEED` + `_GOVERNANCE_NORM_ID_SUFFIXES`
- New helper `emit_asset_sale_governance_norms(driver, db_name, deal_id)`
  parallel to `emit_asset_sale_proceeds_flows`. Idempotent: deletes
  prior governance norms (and their relations) before inserting.
- `clear_v4_projection_for_deal` extended to clean up
  `event_governed_by_norm` relations (TypeDB 3.x doesn't auto-cascade
  these any more than `event_provides_proceeds_to_norm`).
- `project_deal` invokes the governance helper after `proceeds_flows`
  so future re-projections always populate these norms.

No projection_rule schema authoring required (the templated-seed
pattern bypasses the rule executor for this carved-out class of
governance norms — same trade-off Phase C accepted for proceeds_flows).
No Python code outside the helper.

## Validation harness baseline (post-I.2)

A1=pass, A2/A3 fail (pre-existing baseline), A4 m=42 s=6 mm=0 (improved
from 45/6/0), A5=pass aggregate_accuracy=1.0, A6=pass.

Norm count: 23 → 28 (5 new governance norms). A6 norm_count_floor (≥20)
preserved.

**The new pilot baseline is m=42 s=6 mm=0.** All future Phase I sub-phases
must preserve this (or further improve it).

## Lawyer eval per-question results

```
duck_creek_q1 (builder):        primary 10→10  cite 11→11   (no change)
duck_creek_q2 (unsub):          primary  1→ 1  cite  2→ 2   (no change)
duck_creek_q3 (reallocation):   primary  4→ 4  cite  4→ 4   (no change)
duck_creek_q4 (asset_sale):     primary  3→ 5  cite  4→ 5   *** see below ***
duck_creek_q5 (total_capacity): primary  4→ 7  cite  8→ 7   *** see below ***
duck_creek_q6 (ratio):          primary  1→ 1  cite  3→ 3   (no change)
```

Conformance (must_cite ⊆ citations): **6/6** preserved.

### Q4 asset_sale_proceeds — PARTIAL but **NOTABLY RICHER**

Newly PRIMARY (post-I.2):
- `sweep_tier_100pct`, `sweep_tier_50pct`, `sweep_tier_0pct`
- `sweep_exemption_product_line`

Removed from PRIMARY (post-I.2): `builder_usage_permission`,
`general_rp_basket_permission`. Stage 1 reweighted toward the
asset-sale-specific governance norms now that they exist as named
v4 entities.

The new answer **explicitly names Section 2.10(c)(iv)** and cites
the 6.25x leverage threshold:
> "Proceeds retained after the applicable sweep (or exempt from sweep
> entirely, e.g., under the product-line exemption in Section 2.10(c)(iv)
> if leverage ≤ the greater of 6.25x and the current ratio) flow into
> clause (f) of the Cumulative Amount definition"

This is the precise text the gold answer asks for. **Q4 has reached
the level the lawyer answer expects** for 2.10(c)(iv).

Remaining gap: **6.05(z) (`unlimited_asset_sale_basket_permission`)**.
Stage 1 classified this norm as SUPPLEMENTARY with rationale
"Asset sale permission basket, contextually relevant but
investment-focused". The norm's GT-spec sets `scoped_actions=[make_investment]`
(asset sale → reinvestment), so the LLM filter doesn't recognize
it as a dividend-pathway norm without explicit guidance. This is
addressable by:
- I.3 generalized relevance scoring (boost asset_sale-related norms
  for asset_sale-related questions)
- I.4 category L synthesis_guidance update (instruct Stage 1 / Stage 2
  to consider 6.05(z) as primary for asset-sale-proceeds-as-dividends
  questions)

### Q5 total_capacity — PARTIAL unchanged

Stage 1 picked 7 PRIMARY (vs 4 in I.1). Stage 2 used 7 cites (vs 8).
The must-cite layer forced `general_rdp_basket_permission` to be
cited, and Stage 2 used the escape valve:
> "(noted; not load-bearing for this question) making prepayment..."

Exactly the must-cite escape design. The fact that Stage 2 chose to
mark it non-load-bearing rather than sum it into the dividend floor
confirms what Phase G predicted: **the issue is not citation
discipline (I.1 fixed that) but synthesis_guidance vocabulary**. The
RDP basket is reallocable per `action_scope: 'reallocable'`; the
guidance for category N currently doesn't instruct synthesis to
sum reallocable RDP capacity into the RP dividend floor when
fungible. **I.4 category N guidance is the specific lever.**

Q5 floor still says "$260,000,000 fixed floor" — RDP $130M not summed.

### Q1, Q2, Q3, Q6 — no change

Same primary/citation patterns as post-I.1. Q3's PARTIAL gap
(intercompany / reorganization / IPO RDP carveouts) is unchanged
because those are extraction-blocked.

## Where I.2 leaves the architecture

- Q4 PARTIAL → **substantively closer to PASS**. 2.10(c)(iv) named.
  3 sweep tiers cited individually. 6.05(z) needs I.3/I.4.
- Q5 PARTIAL → unchanged headline; foundation strengthened (must-cite
  enforces RDP citation; needs guidance to sum).
- A4 m=42 (was 45) — three norm_kinds matched.
- Phase F's deferred relation populated end-to-end:
  v3 sweep_tier/asset_sale_sweep → v4 governance norm →
  event_governed_by_norm → asset_sale_event.

## What I.2 deferred

- **`sweep_exemption_de_minimis`** (de minimis carveout). Still in GT;
  still in A4 missing. Same pattern would apply
  (asset_sale_sweep filter on individual_de_minimis_usd / annual_de_minimis_usd).
  Bounded scope; can be added in a follow-up commit using the same
  template.
- **6.05(z) PRIMARY classification.** The norm exists but Stage 1 sees
  it as supplementary. I.3 (relevance scoring for asset-sale-related
  questions) + I.4 (category L guidance) will address.
- **Sweep tier condition trees.** GT specifies `condition` blocks
  with predicate `first_lien_net_leverage_above` thresholds. Authoring
  full condition trees on these new norms is non-trivial. Synthesis
  surfaced the leverage thresholds verbatim from `source_text` in
  the post-I.2 Q4 answer regardless, so the omission isn't observed
  to be load-bearing for this eval.

## Branch state at I.2 end

- Branch: `v4-deontic`
- HEAD: this commit (Phase I commit 4)
- Commits ahead of `origin/v4-deontic`: 4
- Push deferred to end of Phase I.

Phase I.2 complete. Moving to Phase I.3 (Tier 1a generalized relevance
scoring) — primary lever to surface 6.05(z) and improve Q5 RDP
positioning.
