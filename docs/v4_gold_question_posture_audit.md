# Gold Question Posture Audit

Date: 2026-04-24
Branch: `v4-deontic`
Governing rule: Rule 8.1 (`docs/v4_foundational_rules.md` §VIII) — world
state is per-query input, not stored state.

Audits all 18 gold questions against the structural-vs-evaluated
dichotomy from `docs/v4_deontic_architecture.md` §6.0. Each question
is classified **structural** (no world-state input needed),
**evaluated** (consumer must supply world state), or **problematic**
(can't be answered cleanly under the new posture).

---

## Classification summary

| Source | Structural | Evaluated | Problematic | Total |
|---|---|---|---|---|
| `lawyer_dc_rp` (6 qs) | 4 | 2 | 0 | 6 |
| `xtract_dc_rp_mfn` RP subset (12 qs) | 10 | 2 | 0 | 12 |
| **Total** | **14** | **4** | **0** | **18** |

No problematic questions. Four questions need consumer-supplied world
state to produce a fully-quantified answer; all four can also produce
meaningful structural answers if the consumer omits inputs (components
list without totals, cap formulas without resolved values, etc.).

---

## lawyer_dc_rp — 6 questions

### Q1 — Builder basket composition

> What test is the build-up basket or available amount basket based on and when does the basket start growing?

**Classification:** structural.
**Operation mapping:**
- `describe_norm` on `dc_rp_cumulative_amount` (the builder norm),
  including `condition_tree` null and `capacity_composition =
  computed_from_sources`
- `enumerate_linked` with `entity_type = "builder_source_*"` to list
  the (a)-(l) sub-sources that feed the basket
**Gold answer shape:** lists three capacity-composition sub-sources
(CNI, ECF, EBITDA-FC) plus starter with a "start growing" date. No
resolved-dollar claim; the answer is a structural description of the
computed_from_sources tree. Matches `describe_capacity_structure` /
`enumerate_linked` output cleanly.

### Q2 — Dividend of Unrestricted Subsidiary equity

> Is the Borrower permitted to dividend the equity it owns in Unrestricted Subsidiaries?

**Classification:** structural.
**Operation mapping:**
- `describe_norm` filter by `modality=permission, action_class=make_dividend_payment, object_class=unrestricted_sub_equity`
- Optionally `evaluate_feasibility` with empty `supplied_world_state`
  if the renderer wants a verdict-shaped response
**Gold answer shape:** "Yes, under 6.06(p) ..." — cites a specific
norm and describes what it permits. No world-state dependency.

### Q3 — Reallocation from other covenants

> Are there any investment, prepayment of other debt or other baskets that can be reallocated and used to make restricted payments or dividends?

**Classification:** structural.
**Operation mapping:**
- `trace_pathways(source=action_class:make_investment, target=action_class:make_dividend_payment, direction=forward, max_hops=2)` and `trace_pathways(source=action_class:pay_subordinated_debt, ...)`, renderer merges
- Alternatively `enumerate_linked` with `entity_type=basket_reallocates_to`
**Gold answer shape:** enumerates specific reallocation pathways
(6.09(a) → 6.06(j), 6.03(y) → 6.06(j)) with their caps
($130M / 100% EBITDA). Structural — no world-state dependency.

### Q4 — Asset-sale proceeds to dividends

> Can any asset sale proceeds be used to make dividends?

**Classification:** structural.
**Operation mapping:**
- `trace_pathways(source={kind:"state_predicate", label:"retained_asset_sale_proceeds"}, target={kind:"action_class", label:"make_dividend_payment"}, direction=forward, max_hops=3)`
- `enumerate_linked` for `sweep_tier`, `asset_sale_sweep`,
  `de_minimis_threshold`, and the builder's asset_proceeds_source so
  the renderer can describe sweep mechanics
**Gold answer shape:** structural pathway description — 6.05(z)
conditions, 2.10(c)(iv) exemption, sweep tiers, de minimis thresholds.
No world-state dependency for the structural enumeration; however, a
follow-up "given current FLN leverage of X, which tier applies" is an
evaluated variation that isn't asked here.

### Q5 — Total quantifiable dividend capacity

> Determine the total amount of quantifiable dividend capacity.

**Classification:** **evaluated**.
**Operation mapping:**
- `evaluate_capacity(action_class=make_dividend_payment, include_reallocated_capacity=true, quantification_mode=total, supplied_world_state={consolidated_ebitda_ltm: X})`
**Gold answer shape:** mixed. Gold reports `$520m (or 409.9% of
EBITDA)` — both a dollar figure and a percentage-of-EBITDA. The
dollar figure requires EBITDA to resolve (`$520M` is 4 × `$130M` floor
when EBITDA × 100% per basket is below `$130M`, which holds when
consolidated EBITDA is roughly ~$127M per the back-solve of 4 ×
$130M / 4.099). Without EBITDA supplied, the operation returns
structural components list only — `total_usd` is null and the response
documents which grower-pct components were unresolved.

### Q6 — Ratio-basket feasibility with hypothetical 6.0x ratio

> If the Borrower owns an asset/business division that has assets worth $200m, but EBITDA of such business is negative, can the Borrower dividend the asset/business division to shareholders if the First Lien Net Leverage Ratio is 6.0x?

**Classification:** **evaluated**.
**Operation mapping:**
- `evaluate_feasibility(action_class=make_dividend_payment, object_class=null, supplied_world_state={proposed_amount_usd: 200000000, ratio_snapshot_first_lien_net_leverage: 6.0, is_no_worse_pro_forma: true})`
**Gold answer shape:** "Yes, because the Ratio RP basket 6.06(o)
permits such transaction as long as the First Lien Net Leverage
Ratio, even if above 5.75x, is no worse." The verdict depends on the
supplied ratio + the `is_no_worse_pro_forma` flag. Pure Rule 8.1
evaluated operation — consumer supplies the hypothetical state,
Valence returns the verdict with condition-evaluation trace.

---

## xtract_dc_rp_mfn — RP subset (questions 1-12)

### Q1 — Three alternative builder tests + starter

> What are the three alternative tests for the build-up basket and what is the starter amount?

**Classification:** structural.
**Operation mapping:** same as lawyer Q1 (`describe_norm` +
`enumerate_linked` over builder_source_*).
**Gold answer shape:** describes three tests with their fiscal-period
semantics + the `$130m/100% EBITDA` starter. The "$130m/100% EBITDA"
is the structural cap formula, not a resolved dollar claim.

### Q2 — Additional builder sources

> What additional sources feed into the build-up basket beyond the three tests and starter amount?

**Classification:** structural.
**Operation mapping:** `enumerate_linked` with `entity_type=builder_source_*`
returning clauses (c) through (l) including the GT's decomposed
sub-components (Retained Declined ECF / Declined Asset Sale / Sale
Leaseback / Investment returns / etc.).
**Gold answer shape:** enumerates 9+ additional sources. Structural.

### Q3 — Builder basket conditions

> Are there any conditions or blockers to usage of the build-up basket for restricted payments?

**Classification:** structural.
**Operation mapping:** `describe_norm` on `dc_rp_cumulative_amount`
+ check `condition_tree == null`.
**Gold answer shape:** "No, there are no conditions." A structural
"empty set" response.

### Q4 — Reallocatable baskets

> Which baskets can be reallocated for restricted payments?

**Classification:** structural.
**Operation mapping:** `enumerate_linked` for `basket_reallocates_to`
relations targeting the general_rp_basket, or `filter_norms` for
norms with `action_scope=reallocable`.
**Gold answer shape:** lists general RDP + general investment baskets
with their cap formulas. Structural.

### Q5 — Size of general RP basket

> What is the size of the general RP basket?

**Classification:** structural.
**Operation mapping:** `get_attribute(entity_ref=general_rp_basket, attribute_name=cap_usd)` and likewise for `cap_grower_pct`.
**Gold answer shape:** "$130m/100% EBITDA" — the cap formula in raw
form, not a resolved dollar. Structural.

### Q6 — Management/employee equity repurchase limits

> What are the limits on management/employee equity repurchases for restricted payments?

**Classification:** structural.
**Operation mapping:** `describe_norm` on `management_equity_basket` +
`enumerate_linked` for carryforward/carryback related norms.
**Gold answer shape:** "$20m/15% EBITDA per fiscal year. Up to
$20m/15% EBITDA may be carried forward and up to $20m/15% EBITDA may
be carried back." Structural description of the cap + carry mechanics.

### Q7 — Ratio test governing unlimited ratio RP basket

> What ratio test governs the unlimited ratio restricted payments basket?

**Classification:** structural.
**Operation mapping:** `describe_norm` on `ratio_basket_permission`
returning the condition tree: `first_lien_net_leverage_at_or_below(5.75) OR pro_forma_no_worse(first_lien)`.
**Gold answer shape:** "First Lien Leverage Ratio does not exceed
5.75x or if the Ratio does not get worse." Structural description of
the condition.

### Q8 — Asset sale proceeds for RPs

> Can asset sale proceeds be used for restricted payments?

**Classification:** structural.
**Operation mapping:** similar to lawyer Q4 — `trace_pathways` from
state predicate to action.
**Gold answer shape:** structural description plus an editorial note
("the Xtract report notes this needs to be removed since it
contradicts the mandatory prepayment provisions"). The editorial is
a gold-answer commentary, not a Valence-answerable claim; Valence
returns the structural pathway and leaves the commentary to the
renderer / human reviewer.

### Q9 — Dividend of Unrestricted Subsidiary equity

> Is the Borrower permitted to dividend the equity it owns in Unrestricted Subsidiaries?

**Classification:** structural. Same as lawyer Q2.

### Q10 — Post-IPO RP permitted amount

> What restricted payments are permitted after an IPO?

**Classification:** structural (description of the cap formula).
Becomes **evaluated** if the consumer asks for the dollar total given
specific IPO proceeds + market cap.
**Operation mapping:**
- Structural: `describe_norm` on `6.06(q) post_ipo_basket` returning
  the formula `7% per annum of IPO proceeds + 7% per annum of market
  cap`
- Evaluated: `evaluate_capacity(action_class=make_dividend_payment, supplied_world_state={ipo_proceeds_usd: X, market_cap_usd: Y, qualified_ipo_has_occurred: true})`
**Gold answer shape:** "not to exceed per annum the sum of 7% of IPO
proceeds and 7% of market cap." Structural formula; Q10 as asked
doesn't require the resolved dollar. Lists the formula only.

### Q11 — Parent company expense RP limitation

> Is there a limitation on the RP basket for payment of parent company expenses?

**Classification:** structural.
**Operation mapping:** `describe_norm` on `6.06(k) parent_overhead`
returning its (null) scope restriction.
**Gold answer shape:** "NOT limited to those attributed to ownership
of Borrower." Structural observation (no condition on the norm) plus
editorial concern from Xtract. Structural.

### Q12 — Day-One RP capacity total

> What is the total Day One restricted payments capacity?

**Classification:** **evaluated**. Same shape as lawyer Q5.
**Operation mapping:** `evaluate_capacity(action_class=make_dividend_payment, include_reallocated_capacity=true, quantification_mode=total, supplied_world_state={consolidated_ebitda_ltm: X})`.
**Gold answer shape:** `$520m (or 409.9% of EBITDA)` plus components
list. Evaluated; structural components are returned when world state
is omitted.

---

## Problematic questions

None. Every question maps to an operation in the §6.0 operations
schema without requiring a persisted-world-state design.

## Questions needing reformulation or deferral

None require reformulation for posture compliance. Two editorial
observations to note:

- xtract Q8 and Q11 include "Xtract report flags this as a concern"
  commentary in their gold answers. Valence's operation returns the
  structural content; the editorial observation is a
  renderer-layer / reviewer-layer addition and is not itself an
  operation target. Acceptance-test scoring should match on the
  structural content, not the editorial commentary.

- xtract Q8 gold answer says "this needs to be removed since it
  contradicts the mandatory prepayment provisions." That's a claim
  about agreement internal consistency, not about Valence's output.
  Acceptance-test scoring against Q8 should compare the structural
  pathway description, not the consistency judgment.

## Proposed representative world state for acceptance-test setup

Four evaluated questions (lawyer Q5, Q6; xtract Q10, Q12) require
consumer-supplied world state. Proposed fixture:

```yaml
# Representative world state for Duck Creek acceptance test (Prompt 13)
#
# Values chosen to resolve the evaluated gold questions to the exact
# numerical claims in their gold answers. Backed out from the gold
# data where possible; otherwise reasonable closing-date assumptions.

dc_acceptance_test_baseline:
  # ── Financial figures ──
  # Back-solved from xtract Q12 gold "$520m (or 409.9% of EBITDA)":
  # 4 × $130m floor = $520m implies 100% × EBITDA < $130m per basket,
  # and 4×$130m / $520m = 1.0, and $520m/EBITDA = 4.099 → EBITDA ≈ $126.9m.
  # Rounded to $127m for clean arithmetic.
  consolidated_ebitda_ltm: 127_000_000

  # Conservative baseline snapshot — First Lien Net Leverage well below the
  # RP basket 5.75x threshold at closing. Adjusted upward per Q6 for its
  # specific hypothetical.
  ratio_snapshot_first_lien_net_leverage: 4.50
  ratio_snapshot_senior_secured_leverage: 5.00
  ratio_snapshot_total_leverage: 5.50

  # Post-IPO fields (xtract Q10). Pre-IPO at closing; overridden per-question.
  qualified_ipo_has_occurred: false
  ipo_proceeds_usd: 0
  market_cap_usd: 0

  # Deontic gates — baseline healthy state.
  no_event_of_default_exists: true
  pro_forma_compliance_financial_covenants: true

  # J.Crew blocker — no pending Unsub designation with Material IP
  unsub_would_own_or_license_material_ip_at_designation: false

  # Asset-sale predicates (inactive at baseline)
  is_product_line_or_line_of_business_sale: false
  individual_proceeds_at_or_below: true
  annual_aggregate_at_or_below: true

# Q6-specific overlay — Borrower proposes a $200m business-division
# dividend and states the ratio is 6.0x on a no-worse basis.
dc_q6_hypothetical:
  extends: dc_acceptance_test_baseline
  overrides:
    proposed_amount_usd: 200_000_000
    ratio_snapshot_first_lien_net_leverage: 6.00
    is_no_worse_pro_forma: true

# Q10-specific overlay — post-IPO state
dc_q10_post_ipo:
  extends: dc_acceptance_test_baseline
  overrides:
    qualified_ipo_has_occurred: true
    ipo_proceeds_usd: 500_000_000      # representative mid-size IPO
    market_cap_usd: 2_000_000_000      # representative mid-size issuer
```

Note: these are per-query inputs (Rule 8.1), not persistent graph state.
The fixture lives in `app/data/duck_creek_rp_world_state.yaml` as a
reference input consumers (including the acceptance-test runner) load
and pass as `supplied_world_state`. No loader code writes these values
into `valence_v4`.

Back-solved EBITDA of ~$127M is a plausible figure for Duck Creek
Technologies at its 2025 leveraged-buyout close; Q12's gold of $520M
at 409.9% of EBITDA implies precisely this order of magnitude. If the
real EBITDA is meaningfully different, acceptance-test Q12 output
won't match the gold and the discrepancy is a signal to verify the
gold answer itself.

## Operation coverage implied by this audit

From the classification, Prompt 11 must implement at least the following
from the §6.1 list to clear the acceptance test:

- `describe_norm` — **critical** (Q1, Q2, Q5, Q7, Q10, Q11 + more)
- `enumerate_linked` — **critical** (Q1, Q2, Q4, Q6)
- `trace_pathways` — **critical** (Q3, Q4, Q8 — for both action-class
  and state-predicate anchors)
- `evaluate_feasibility` — **critical** (Q6, and fallback for yes/no
  variants of Q2, Q9)
- `evaluate_capacity` — **critical** (Q5 / Q12)
- `get_attribute` — **useful** (Q5 — "size of general RP basket" is a
  one-attribute fetch)
- `filter_norms` — **useful** (Q4 reallocatable filter; can fall back
  to `enumerate_linked`)

Five of the 11 operations are critical for Duck Creek. The remaining
six (`enumerate_defeaters`, `describe_relation`, `lookup_definition`,
`enumerate_patterns`) are not required for the 18-question set but
would be needed for extensions (MFN, DI, J.Crew-pattern detection).
