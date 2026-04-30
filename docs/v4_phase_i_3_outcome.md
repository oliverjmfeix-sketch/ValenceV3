# Phase I.3 outcome — generalized relevance scoring (Tier 1a)

> Phase I.3 implements **Phase G's Tier 1a**: replaces Phase G commit
> 3's bounded `action_scope`-only sort with question-aware per-norm
> relevance scoring. Two commits on `v4-deontic`.

## Summary

| Workstream | Outcome |
|---|---|
| Implementation | ✓ commit `fc3d9ef` |
| Validation harness | ✓ baseline preserved (A4 m=42 s=6 mm=0 unchanged) |
| Lawyer eval re-run | 6/6 OK; cost $0.8333; latency 332s |
| Q5 builder breakdown | ✓ NEW — Stage 1 PRIMARY rose to 11; all 6 builder_source norms cited individually |
| Q4 carveout naming | ✗ regression — 2.10(c)(iv) and 6.05(z) no longer named |
| Stop-loss | ✓ no PASS→PARTIAL/FAIL regression on any question |

Total cost: $0.8333 (eval). Cumulative Phase I: $2.3921.

## Implementation

`app/services/synthesis_v4_fetch.py`:
- New `_relevance_tokenize()` — splits on non-alphanumeric AND on
  underscore so `unlimited_asset_sale_basket_permission` produces
  individual tokens {unlimited, asset, sale, basket, permission}.
- New `_compute_norm_relevance_score(norm, question, category_keywords)`
  with deterministic additive components (see commit message for
  weights).
- `fetch_norm_context(...)` signature extended with optional
  `question` and `category_keywords` kwargs. Sort replaced with
  `sorted(..., key=score, reverse=True)`. Backward-compatible:
  legacy callers without args fall back to tier-1-only scoring.

`app/services/synthesis_v4.py`:
- `synthesize_one_question()` collects `category_keywords` from
  `route.matched_categories[*].keywords` and passes through to
  `fetch_norm_context()`.

## Validation harness baseline (post-I.3)

A1=pass, A2/A3 fail (pre-existing), A4 m=42 s=6 mm=0 (preserved
from I.2), A5=pass aggregate_accuracy=1.0, A6=pass.

Synthesis-only change. Projection state untouched.

## Lawyer eval per-question results

```
duck_creek_q1 (builder):        primary 10→ 9  cite 11→ 9   PASS preserved
duck_creek_q2 (unsub):          primary  1→ 1  cite  2→ 4   PASS preserved
duck_creek_q3 (reallocation):   primary  4→ 4  cite  4→ 4   PARTIAL unchanged
duck_creek_q4 (asset_sale):     primary  5→ 2  cite  5→ 5   PARTIAL (regressed naming)
duck_creek_q5 (total_capacity): primary  7→11  cite  7→11   PARTIAL (richer)
duck_creek_q6 (ratio):          primary  1→ 1  cite  3→ 4   PASS preserved
```

Conformance (must_cite ⊆ citations): **6/6** preserved.

### Q5 — load-bearing improvement

Stage 1 promoted **all 6 builder_source norms** to PRIMARY (was 4 in
I.2). Stage 2 cited each individually with explicit annotations:

> "(1) General RP Basket [Section 6.06(j), p.196]: general-purpose,
> floored at the greater of $130,000,000 and 100% of Consolidated
> EBITDA … (2) Builder Basket / Cumulative Amount [Definition of
> Cumulative Amount, p.32]: general-purpose, computed as (a) the
> SUM of the starter amount … PLUS (b) the greatest of 50% of
> cumulative Consolidated Net Income, the Available Retained ECF
> Amount, and 140% of LTM Consolidated EBITDA … PLUS (c) 100% of
> equity proceeds…"

Builder basket structure now decomposed correctly. RDP basket still
cited but **floor still says $260M** — the dividend sum doesn't
include RDP $130M. This last gap is a synthesis_guidance instruction
issue (category N's vocabulary doesn't tell Stage 2 to sum reallocable
RDP into the RP floor when fungible). **I.4 lever applies.**

### Q4 — regressed carveout naming, retained sweep tier inline

Stage 1 dropped 4 governance norms (3 sweep tiers + sweep_exemption_product_line)
from PRIMARY in this run. Cause is mixed:
- The new sort positioned `builder_source_retained_asset_sale_proceeds`
  and `builder_usage_permission` higher (they have strong
  question-keyword overlap with "asset sale proceeds" + "dividends");
- Stage 1's classifier reasoning shifted under the new payload order
  toward "dividend permission" interpretation that strict-classified
  sweep_tier and sweep_exemption norms as "context, not dividend
  permission" / "affects proceeds availability indirectly".

The post-I.3 answer **still cites all 3 sweep tiers** and describes
the leverage-based sweep mechanics correctly. But it no longer
explicitly names Section 2.10(c)(iv) (which I.2's answer named
verbatim). 6.05(z) still not surfaced.

Compared to:
- Phase G baseline (pre-I.1): Q4 PARTIAL, no carveouts named
- Post-I.1: PARTIAL, no carveouts named
- Post-I.2: PARTIAL but **2.10(c)(iv) named** ✓
- Post-I.3: PARTIAL, sweep tiers cited, **2.10(c)(iv) lost**

The gap is recoverable via I.4 (category L synthesis_guidance update
to point Stage 1/2 at the new v4 governance norms by name). The
norms exist in the graph; the question is whether Stage 1's filter
prompt gives them sufficient priority.

### Q1, Q2, Q3, Q6 — PASS/PARTIAL preserved

- Q1: cite 11 → 9 (-2). Lost `builder_rdp_basket_permission` and
  `builder_source_other_debt_conversion` from citations. Answer
  remains comprehensive; covers gold's "build-up mechanism" enumeration.
  PASS preserved.
- Q2: cite 2 → 4 (+2). Added general_rp_basket and ratio_rp_basket.
  No semantic regression. PASS preserved.
- Q3: unchanged headline. PARTIAL with same RP↔RDP framing as I.1/I.2.
- Q6: cite 3 → 4 (+1). PASS preserved.

## Stop-loss assessment

The plan's stop-loss criterion: "If sub-phase I.1 or I.3 regresses
any PASS question to PARTIAL or FAIL, halt and revert that
sub-phase."

- Q1: PASS → PASS ✓
- Q2: PASS → PASS ✓
- Q6: PASS → PASS ✓

No PASS→PARTIAL regression. Q4 went PARTIAL → PARTIAL (different
aspects covered/missed). Q5 went PARTIAL → PARTIAL (richer answer).

Validation harness baseline preserved (A4 m=42 s=6 mm=0).

**I.3 ships.** The Q4 carveout-naming regression is bounded: the
norms exist, are properly typed, and remain SUPPLEMENTARY (not
SKIP). I.4's category L synthesis_guidance rewrite is the targeted
recovery lever — it can instruct Stage 1 to treat
`unlimited_asset_sale_basket_permission` and
`sweep_exemption_product_line` as PRIMARY for asset-sale-proceeds
questions.

## Cost summary

- Eval: $0.8333 (vs I.2's $0.8047; +3.6%)
- Latency: 332s (vs 325s; +2.2%)
- Implementation: $0
- **Phase I.3 total: $0.8333**

Cumulative Phase I: $2.3921. Within budget; under Phase F
($3.68) and Phase E ($4.32).

## What I.3 changed in the architecture

- **Question awareness in payload sort.** Previously, the payload
  order was a fixed function of norm attributes (Phase G commit 3's
  4-tier sort). Now it's a function of question text + matched
  categories + norm attributes. The same norm context can be
  reordered differently for different questions.
- **Backward compatibility preserved.** Legacy callers without
  question/category_keywords get the tier-1-only score, which
  approximates Phase G's bounded behavior.
- **`_relevance_tokenize()` is reusable.** Future synthesis-side
  components can use the same tokenization (e.g. category L's
  rewritten guidance can reference these tokens explicitly).

## What I.3 deferred

- **Q4 carveout naming** — the architecture can surface them; Stage 1
  needs explicit guidance to treat them as PRIMARY. I.4 category L
  rewrite is the lever.
- **Q5 RDP sum** — must-cite forces citation; relevance scoring puts
  RDP at score 0.37 (mid-priority); but synthesis_guidance for category
  N still doesn't instruct sum semantics. I.4 category N may need
  a sentence amendment to make the "sum reallocable RDP into RP floor"
  rule literal.
- **Score weight tuning.** Current weights are reasonable but not
  optimized; future iterations can tune via per-question outcome
  evals.

## Branch state at I.3 end

- Branch: `v4-deontic`
- HEAD: this commit (Phase I commit 6)
- Commits ahead of `origin/v4-deontic`: 6
- Push deferred to end of Phase I.

Phase I.3 complete. Moving to Phase I.4 (synthesis_guidance v3→v4
rewrite for eval-priority categories — primary lever to recover
Q4 carveout naming and address Q5 RDP sum semantics).
