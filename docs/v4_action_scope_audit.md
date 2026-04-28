# v4 Audit — `action_scope` Semantics for Capacity Contributions

> **Phase B-era doc (2026-04-24).** References `deontic_projection.py`
> emit behavior. Phase C completed 2026-04-28; `deontic_projection.py`
> was deleted in Commit 4. Equivalent emission now happens in
> `projection_rule_executor.py` via `rule_conv_builder_*` rules' relation
> templates. Audit conclusions remain accurate; the `action_scope`
> attribute and its values are unchanged.

Date: 2026-04-24
Scope: pre-Prompt-10 diagnostic on how `action_scope` should be populated
for capacity-contribution norms (builder sub-sources + analogues).
Documentation-only. No schema / code / GT changes.

---

## 1. Architecture doc §4.1 — verbatim

From `docs/v4_deontic_architecture.md` §3.6 (norm attribute list) and §4.1
(attribute declarations):

> `action_scope` ∈ {`specific`, `general`, `reallocable`}

§4.1 TypeQL declaration:

```tql
attribute action_scope, value string;          # specific|general|reallocable
```

**The architecture doc does not define the three values.** §3.6 enumerates
them as an enum but provides no per-value gloss. §9's classification-
measurement section adds: "`action_scope` (`specific` / `general` /
`reallocable`). Measurement: human-labelled scope for every projected
**permission** in Duck Creek. Compare against the label." — explicitly
anchored to permissions.

Operational definitions from `app/services/classification_measurement.py`
V2 prompt (the authoritative stand-in since the doc is silent):

- **specific**: "the norm applies to a narrow, named action or set of
  closely-related actions (e.g., tax distributions only, management
  equity buyouts only, post-IPO dividends only)."
- **general**: "the norm applies broadly across multiple RP-like action
  classes without discrimination (e.g., a ratio basket available for
  dividends OR repurchases OR RDPs)."
- **reallocable**: "the norm's capacity can be redirected from its
  primary action class into other RP actions via an explicit reallocation
  mechanism (e.g., a general RP basket that can reallocate to prepay
  subordinated debt)."

All three definitions describe the norm's applicability pattern to
actions. **None addresses capacity-contribution norms as a distinct
category.**

## 2. GT query — capacity-contribution norms (Step 2)

All 20 norms with an outgoing `norm_contributes_to_capacity` edge, their
scope/composition, and their parent:

| norm_id | norm_kind | scope | capacity_composition | parent |
|---|---|---|---|---|
| dc_rp_6_06_q_post_ipo_ipo_proceeds_component | post_ipo_basket_ipo_proceeds_component | specific | additive | dc_rp_6_06_q_post_ipo_basket |
| dc_rp_6_06_q_post_ipo_market_cap_component | post_ipo_basket_market_cap_component | specific | additive | dc_rp_6_06_q_post_ipo_basket |
| dc_rp_cumulative_amount_a_starter | builder_source_starter | specific | additive | dc_rp_cumulative_amount |
| dc_rp_cumulative_amount_b_aggregate | builder_source_b_aggregate | specific | computed_from_sources | dc_rp_cumulative_amount |
| dc_rp_cumulative_amount_b_x_cni | builder_source_cni | specific | additive | dc_rp_cumulative_amount_b_aggregate |
| dc_rp_cumulative_amount_b_y_ecf | builder_source_ecf | specific | additive | dc_rp_cumulative_amount_b_aggregate |
| dc_rp_cumulative_amount_b_z_ebitda_fc | builder_source_ebitda_fc | specific | computed_from_sources | dc_rp_cumulative_amount_b_aggregate |
| dc_rp_cumulative_amount_b_z_ebitda_component | builder_source_ebitda_component | specific | additive | dc_rp_cumulative_amount_b_z_ebitda_fc |
| dc_rp_cumulative_amount_b_z_fixed_charges_component | builder_source_fixed_charges_component | specific | additive | dc_rp_cumulative_amount_b_z_ebitda_fc |
| dc_rp_cumulative_amount_c_declined_ecf | builder_source_declined_ecf | specific | computed_from_sources | dc_rp_cumulative_amount |
| dc_rp_cumulative_amount_d_declined_asset_sale | builder_source_declined_asset_sale | specific | computed_from_sources | dc_rp_cumulative_amount |
| dc_rp_cumulative_amount_e_sale_leaseback | builder_source_sale_leaseback | specific | computed_from_sources | dc_rp_cumulative_amount |
| dc_rp_cumulative_amount_f_retained_asset_sale | builder_source_retained_asset_sale | specific | computed_from_sources | dc_rp_cumulative_amount |
| dc_rp_cumulative_amount_g_joint_venture_returns | builder_source_joint_venture_returns | specific | computed_from_sources | dc_rp_cumulative_amount |
| dc_rp_cumulative_amount_h_unsub_redesignation_fmv | builder_source_unsub_redesignation_fmv | specific | computed_from_sources | dc_rp_cumulative_amount |
| dc_rp_cumulative_amount_i_receivables_royalty_license | builder_source_receivables_royalty_license | specific | computed_from_sources | dc_rp_cumulative_amount |
| dc_rp_cumulative_amount_j_investment_returns | builder_source_investment_returns | specific | computed_from_sources | dc_rp_cumulative_amount |
| dc_rp_cumulative_amount_k_cumulative_deferred_revenues | builder_source_deferred_revenues | additive | additive | dc_rp_cumulative_amount |
| dc_rp_cumulative_amount_l_other | builder_source_other | specific | computed_from_sources | dc_rp_cumulative_amount |
| dc_rp_cumulative_amount_m_netting | builder_source_netting | specific | additive | dc_rp_cumulative_amount |

**Observation**: **20/20 contributors authored `specific`**, zero nulls.
Two authoring shapes: post-IPO component family (2 norms) contributes to
a post-IPO parent; the Cumulative Amount family (18 norms) contributes to
the builder-basket aggregate (directly or through 2 intermediate
aggregates). No drift. Full scoped_actions distribution from the YAML:

| Count | scoped_actions set |
|---|---|
| 17 | `[make_dividend_payment, make_investment, pay_subordinated_debt, repurchase_equity]` |
| 2 | `[make_dividend_payment]` |
| 1 | `[]` |

Even when contributors scope to the 4-action Cumulative Amount usage set,
the YAML author chose `specific` (not `general`). "Narrow named set" in
the operational definition is interpreted elastically — a closed 4-action
set counts as narrow enough as long as it's not "any RP action."

## 3. Peer non-contribution norms (Step 3)

43 norms without a `norm_contributes_to_capacity` outgoing edge, grouped
by `action_scope`:

| action_scope | count | representative norm_kinds |
|---|---|---|
| `general` | 2 | `builder_usage_permission`, `builder_basket_aggregate` |
| `reallocable` | 4 | `general_rp_basket_permission`, `general_rdp_basket_permission`, `cumulative_amount_rdp_usage_permission`, `general_investment_basket_permission` |
| `specific` | 37 | `intercompany_permission`, `management_equity_basket_permission`, `tax_distribution_basket_permission`, `holdco_overhead_basket_permission`, `ratio_basket_permission`, `unsub_distribution_basket_permission`, `post_ipo_basket_permission`, 30 more |

**Interpretation**:
- `general` is reserved for the *usage permissions* at the top of a
  computed-from-sources hierarchy (the norms that say "you may distribute
  up to the Cumulative Amount").
- `reallocable` is reserved for baskets whose capacity can flow across
  action classes via explicit `reallocates_to` edges.
- `specific` is the default — applies both to narrow single-purpose
  baskets (tax, mgmt equity, holdco overhead) AND to all 20 capacity
  contributors.

The distinction is **"does this norm govern action selection at the
point of use?"**. Usage permissions for broad basket families get
`general`. Everything narrower gets `specific`, whether it's a
single-purpose basket or a capacity contributor.

## 4. Function library usage (Step 4)

Grep `app/data/deontic_*_functions.tql` for `action_scope`: **zero matches.**

No TypeDB function body reads the attribute during capacity aggregation,
condition evaluation, or defeater resolution. The attribute is pure
classification metadata — filtering/dispatching over it is reserved for
the (not-yet-built) operations layer.

Seed + schema references (expected, not function usage):
- `rp_deontic_mappings.tql` — mappings declare `default_action_scope_kind`
  per entity type. Projection inherits the value.
- `schema_v4_deontic.tql` — the attribute declaration.

## 5. Operations-layer expected usage (Step 5)

Python references in `app/services/`:

| File | Usage |
|---|---|
| `deontic_projection.py` | Emits `action_scope` on every projected norm. Reads from mapping's `default_action_scope_kind`. Current sub-source emission hardcodes `"general"` (the Prompt 08 Fix 5 default). |
| `classification_measurement.py` | The measurement target. V2 prompt defines the three values. D4 grading compares extracted action_scope to GT. |
| `validation_harness.py` | A4 round-trip reads it as one of the attributes to diff between extracted and GT. |

No query engine reads it (operations layer not yet built). Per the
architecture doc §6 operations schema, `describe_norm` and `filter_norms`
list it as a filter parameter for future intent-parser dispatch, but no
implementation exists.

**Consequence:** the ruling does not affect system behavior today. It
only affects classification-measurement agreement and A4 mismatch counts.
Operations-layer implementation in Prompt 11+ will use it for querying
"find permissions of scope X for action Y," at which point the semantics
need to be nailed down.

## 6. Candidate evaluation

### Candidate A — `specific` (GT authoring convention)

**Definition fit.** Requires accepting "specific" as elastic enough to
cover both narrow single-purpose baskets (tax distributions) and 4-action
capacity contributors. The operational definition admits this:
"set of closely-related actions" is the phrase. A closed 4-action set
qualifies.

**Consistency.** 20/20 GT contributors use it. Fix 2's builder sub-source
tuple match would flip from 9/17 (current, with projection emitting
`"general"`) to 17/17 on action_scope if projection inherited `"specific"`.

**Behavior impact.** Operations layer (future) querying "give me all
specific-scope norms for make_dividend_payment" would include sub-sources
— which is correct: they earmark capacity to that action.

### Candidate B — `general` (current projection emission)

**Definition fit.** "Applies broadly across multiple RP-like action
classes without discrimination." Builder sub-sources DON'T apply to
actions directly — they contribute capacity to a parent. Using `general`
for a contributor confuses the query "list general-scope permissions" —
the builder sub-sources would show up even though they aren't themselves
usable at a point of action.

**Consistency.** Projection emits it; GT doesn't. 20/20 disagreement.

**Behavior impact.** Operations layer asking "what actions can I take
with `general` scope" would over-count by 9 builder sub-sources per deal.

### Candidate C — new enum value (`contributory` / `n_a` / `inherited`)

**Taxonomic case.** Capacity contributors ARE a distinct norm category —
they don't govern action selection, they supply capacity. A fourth value
would acknowledge this directly.

**Cost.**
- Schema: additive change to the enum comment in §4.1 (no functional
  impact; enum isn't enforced)
- GT: 20 norm edits to switch from `specific` to `contributory`
- expected_norm_kinds seed: possibly add it to validation
- projection: add a branch in `_project_builder_sub_sources`
- classification_measurement V3 prompt: new enum value

**Benefit.** Cleaner semantics in the operations layer. Queries
"list specific-scope permissions" wouldn't include contribution norms.

**Risk.** Re-litigates a settled classification. GT has already stabilized
on `specific` for all 20. Adding a fourth value after the baseline
measurements are calibrated opens up re-authoring work with no behavioral
payoff until the operations layer exists.

## 7. Recommended ruling

**Candidate A — `specific`.**

Rationale, ordered by weight:

1. **GT is the authoritative baseline** (Rule 7.2). 20/20 contributors
   author `specific`. Projection should align with GT, not the other
   way around.

2. **The operational definition admits it.** "Narrow, named action or
   set of closely-related actions" — a closed 4-action set counts.
   Contributors don't apply to actions at all in the "action selection"
   sense, but when they inherit their parent's action set, that set is
   always specific by construction (the parent is at most 4-way).

3. **`general` is actively wrong under the definition.** `general`
   requires "applies broadly across multiple RP-like action classes
   without discrimination." Contributors don't *apply* to actions;
   they *feed* capacity. Using `general` for them conflates two
   different shapes of norm in the same bucket.

4. **The taxonomy gap (Candidate C) is real but not blocking.** A
   `contributory` / `n_a`-for-scope value would cleanly mark contributors
   as a distinct category. It's worth considering post-pilot after
   measuring operations-layer queries and seeing whether the conflation
   of "specific single-purpose basket" with "specific-inheriting
   contributor" ever causes friction. For now, don't expand a settled
   enum.

5. **Zero function-body usage** means no behavioral impact today.
   Correcting projection to emit `specific` for sub-sources is a
   one-line change with no cascading effects.

## 8. Migration cost estimate

**If Candidate A adopted (recommended):**

- Projection edits in `_project_builder_sub_sources`:
  - Change hardcoded `"general"` to `"specific"` on sub-source emission (1 line)
  - Change hardcoded `"general"` on b_aggregate emission (1 line)
  - Re-project Duck Creek (no re-extraction; ~30s)
- GT: no edits (already `specific`)
- Schema: no edits
- Expected classification delta:
  - `action_scope` matched accuracy: 52.9% → ~94% (projected, assuming
    the 8 builder sub-sources flip to correct)
  - A4 mismatched count: drops by ~8

Total: single-file, two-line change in projection. Pure alignment.

**If Candidate B kept (status quo):** no edits, but `action_scope`
measurement stays at 52.9% with the specific-vs-general disagreement
locked in as a known drift.

**If Candidate C adopted (expand enum):** schema comment update + 20 GT
edits + projection branch + V3 prompt + classification measurement
re-calibration. Order of magnitude larger than A.

## Recommendation: **Candidate A**

Change projection's sub-source and b_aggregate `action_scope` emission
from `"general"` to `"specific"`. Aligns with GT authoring (20/20).
Respects the operational definition of `specific`. Small mechanical
change with predictable accuracy delta. Revisit taxonomy expansion
(Candidate C) post-pilot if operations-layer queries reveal the
conflation causes real friction.
