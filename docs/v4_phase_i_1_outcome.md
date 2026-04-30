# Phase I.1 outcome — Stage 2 must-cite layer (Tier 1b)

> Phase I.1 implements **Phase G's Tier 1b**: a must-cite layer that
> makes the Stage 1 PRIMARY → Stage 2 citation hierarchy enforceable
> via prompt + payload discipline. Two commits on `v4-deontic`.

## Summary

| Workstream | Outcome |
|---|---|
| Implementation | ✓ complete (commit `587f614`) |
| Validation harness | ✓ baseline preserved (A1=pass, A4 m=45 s=6 mm=0, A5=pass acc=1.0, A6=pass) |
| Lawyer eval re-run | 6/6 OK; cost $0.7541 (~baseline); latency 305s |
| Must-cite conformance | ✓ 6/6 questions; every PRIMARY norm cited |
| Empirical Q5 progress | RDP norm now cited (was Phase G's load-bearing miss) |

Total cost: $0.7541 (eval). Within budget; comparable to Phase G's
re-run ($0.75).

## Implementation

`app/services/synthesis_v4.py`:

- **`_STAGE2_SYSTEM_TEMPLATE`** — new section "MUST-CITE LIST — STAGE 1
  PRIMARY AUTHORITY" inserted after STRICT RULES, before
  CATEGORY-SPECIFIC ANALYSIS GUIDANCE. Instructs Stage 2 that every
  norm in `must_cite_norm_ids` MUST appear BOTH in
  `reasoning.primary_norms_considered` AND in the `citations` array.
  Provides escape valve: a non-load-bearing norm gets cited with
  `quote: "(noted; not load-bearing for this question)"`.
- **`run_stage2()`** — builds `must_cite_norm_ids = sorted(stage1.primary_norm_ids ∩ context.norms)`
  and injects as a payload key alongside `primary_norms`,
  `supplementary_norms`, `defeaters`, etc.
- **`Stage2Result`** — new `must_cite_norm_ids: list[str]` field.
- **`SynthesisResult.to_json_dict()`** — surfaces both
  `stage1.primary_norm_ids` (sorted) and `stage2.must_cite_norm_ids`
  in eval JSON output for auditability.

Output schema unchanged (`citations` was already required non-empty).

## Lawyer eval per-question results (NEW vs Phase G)

```
duck_creek_q1 (builder):       must_cite=10  cite_new=11 cite_old= 6 (+5)  conformance=YES
duck_creek_q2 (unsub):         must_cite= 1  cite_new= 2 cite_old= 1 (+1)  conformance=YES
duck_creek_q3 (reallocation):  must_cite= 4  cite_new= 4 cite_old= 6 (-2)  conformance=YES
duck_creek_q4 (asset_sale):    must_cite= 3  cite_new= 4 cite_old= 5 (-1)  conformance=YES
duck_creek_q5 (total_capacity):must_cite= 4  cite_new= 8 cite_old=10 (-2)  conformance=YES
duck_creek_q6 (ratio):         must_cite= 1  cite_new= 3 cite_old= 2 (+1)  conformance=YES
```

**Conformance: 6/6 questions** — every Stage 1 PRIMARY norm appears in
Stage 2's citations. The architecture's declared authority hierarchy
is now empirically enforced.

## Per-question behavioral changes

### Q1 builder_basket — PASS preserved, citation breadth ↑

`+5` cites: now lists `builder_source_three_test_aggregate`,
`builder_source_investment_returns`, `builder_source_other_debt_conversion`,
`builder_source_other_equity_proceeds`, `builder_source_retained_asset_sale_proceeds`.
The full builder constellation now appears in citations rather than
just the parent permission. PASS verdict unchanged.

### Q2 unsub_distribution — PASS preserved

`+1` cite: `jcrew_blocker_prohibition` now joins the citation list
(it's the load-bearing prohibition that constrains unsub designation).
PASS unchanged.

### Q3 reallocation — PARTIAL but **richer framing**

`-2` cites (ratio_rdp + ratio_rp baskets, both SUPPLEMENTARY) but
**new explicit RP↔RDP fungibility framing in answer**:
- "the general RDP basket (greater of $130M / 100% Consolidated EBITDA)
  under Section 6.09(a)(I) … has `action_scope: reallocable`,
  consistent with the matching general RP basket under Section 6.06(j)
  … indicating fungibility at the same dollar cap across the two
  covenants."
- "the general investment basket under Section 6.03(y) expressly
  incorporates 'the aggregate total of all amounts available to be
  utilized for Restricted Debt Payments pursuant to Section 6.09(a)'
  as additive investment capacity, confirming that RDP capacity can
  be reallocated into investments."

Tailored carveouts (intercompany / reorganization / IPO) still
missing — **extraction-blocked**, will be addressed by I.5 (additive
schema/questions for post-pilot re-extraction).

### Q4 asset_sale_proceeds — PARTIAL unchanged

Inline sweep tiers cited (5.75x / 5.50x / 0x). 2.10(c)(iv) and 6.05(z)
still not enumerated by section name — **mechanism (c) data
positioning** persists. Will be attacked by I.2 (event_governed_by_norm
projection of these as v4 norms) + I.3 (relevance scoring).

### Q5 total_capacity — PARTIAL but **load-bearing progress**

The single most consequential per-question change in I.1:

`-2` cites (builder_rdp / tax_dist / holdco_overhead) but **new
citation `general_rdp_basket_permission`** — the very norm Phase G's
diagnostic identified as Stage 2 dropping under default payload
positioning. New answer also includes:

> "The General RDP Basket [Section 6.09(a)(I), p.200] (greater of $130M
> or 100% EBITDA) is reallocable and may supplement dividend capacity
> cross-covenant."

The RDP $130M is now **named** in the answer. However, the answer
still concludes "combined dollar floors of at least $260,000,000
before EBITDA growers" — RDP $130M is mentioned as cross-covenant
supplement, not yet **summed** into the dividend floor. This matches
Phase G's prediction that 1b alone does not fully close mechanism (c);
the larger lever is 1a (relevance scoring) + N-category synthesis_guidance
explicit "sum reallocable RDP into RP floor when fungible" instruction
(I.3 / I.4). The architecture now has the RDP norm visible to Stage 2
on every Q5 run — that visibility is the foundation the rest of
Phase I builds on.

### Q6 ratio_basket — PASS preserved

`+1` cite (`unrestricted_sub_equity_distribution_permission`).
Cleaner citation discipline.

## Headline verdict

- Q1, Q2, Q6: **PASS preserved**.
- Q3: **PARTIAL but richer** (explicit RP↔RDP fungibility framing).
- Q4: **PARTIAL unchanged** (mechanism c persists; deferred to I.2 + I.3).
- Q5: **PARTIAL but load-bearing progress** (RDP norm cited; floor sum
  not yet performed; deferred to I.3 + I.4).

No PASS→PARTIAL regression. Validation harness baseline preserved.
Conformance is 100%. Authority hierarchy enforceable.

## Validation harness baseline (post-I.1)

A1=pass, A2/A3/A4 fails are pre-existing baseline (A4 m=45 s=6 mm=0
matches pre-I.1), A5=pass aggregate_accuracy=1.0, A6=pass.

Identical to pre-I.1 baseline; preserved across both commits.

## Cost summary

- Eval: $0.7541 (vs Phase G's $0.7483; +0.8%)
- Latency: 305.1s (vs Phase G's 291.9s; +4.5% — extra citation work)
- Implementation: $0
- **Phase I.1 total: $0.7541**

Cumulative Phase I spend: $0.7541. Well within budget.

## What I.1 changed in the architecture

- **Authority hierarchy is now enforceable across every question.** The
  Phase G finding ("Stage 2's hierarchy is graded; guidance does not
  consistently override LLM attention defaults for citation behavior")
  is now neutralized at the citation layer. The
  `must_cite_norm_ids` payload + system prompt rule ensures Stage 1's
  PRIMARY classification is auditable.
- **Eval JSON now includes per-question audit data.** Both
  `stage1.primary_norm_ids` and `stage2.must_cite_norm_ids` are
  surfaced in `to_json_dict()` output — future eval runs can mechanically
  verify conformance via subset check.
- **Q5 RDP citation chain restored.** Phase G's 3-phase diagnostic
  showed mechanism (c) caused Stage 2 to silently drop
  `general_rdp_basket_permission`. Post-I.1, the norm is cited every
  time. Closing the floor-sum requires complementary changes (I.3/I.4)
  but the substrate is now in place.

## What I.1 deferred (will be picked up by later sub-phases)

- **Q5 floor-sum behavior** — RDP cited but not summed. Needs
  relevance scoring (I.3) to elevate RDP norms higher in payload AND
  category N synthesis_guidance instruction to sum reallocable RDP
  into RP floor when fungible (I.4 partly; arguably needs explicit
  norm-level capacity_composition guidance).
- **Q4 carveout enumeration** — 2.10(c)(iv) / 6.05(z) not surfaced by
  section name. I.2 will project these as v4 norms tied to
  `asset_sale_event` via `event_governed_by_norm`, making them
  must-cite candidates and surfacing them as named norms.
- **Q3 tailored carveouts** — extraction-blocked. I.5 will author
  questions/schema additively for post-pilot re-extraction.

## Branch state at I.1 end

- Branch: `v4-deontic`
- HEAD: I.1 commit 2 (this commit; appended after `587f614`)
- Commits ahead of `origin/v4-deontic`: 2
- **Push deferred to end of Phase I** per locked scope.

Phase I.1 is complete. Moving to Phase I.2.
