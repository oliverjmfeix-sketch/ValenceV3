# v4 function library — graph-native audit

Pre-Prompt-07, post-bundle-1 (commits `93d8031`, `27ecfdd`, `cb8b71d`, `f1c2cf4`, `a72d169`).

Each function across the six deontic function files is scored against three questions:

1. **Could a computed value be an attribute?** (Static computations that don't depend on evaluation context should be cached as graph data.)
2. **Does the function dispatch on string labels in a way that bakes logic into function text?** (Per-label hardcoded logic means adding new labels requires editing the function — violates "dispatch data is graph data.")
3. **Does filtering implicitly encode a classification that should be a typed relation or attribute?**

Verdicts: `graph-native` | `minor-concern` | `needs-restructure` | `stub`.

---

## `deontic_condition_functions.tql`

### `predicate_holds($pred: state_predicate, $ws: event_instance) -> boolean`

**Verdict:** `minor-concern`.

Dispatches on `state_predicate_label`. Seven branches:

- `no_event_of_default_exists` — PILOT STUB, returns true
- `pro_forma_no_worse` — reads `is_no_worse_pro_forma` (boolean) from event_instance
- `first_lien_net_leverage_at_or_below` — reads `threshold_value_double` from state_predicate, reads `ratio_snapshot_first_lien_net_leverage` from event_instance, compares
- `senior_secured_leverage_at_or_below` — same pattern, reads `ratio_snapshot_senior_secured_leverage`
- `total_leverage_at_or_below` — same pattern, reads `ratio_snapshot_total_leverage`
- `incurrence_test_satisfied` — same pattern, reads `ratio_snapshot_first_lien_net_leverage` (ignores `reference_predicate_label` today; noted)
- `first_lien_net_leverage_above` — strict-greater-than variant
- `individual_proceeds_at_or_below`, `annual_aggregate_at_or_below` — compound greater-of pattern (read `threshold_value_double`, `threshold_grower_pct`, `consolidated_ebitda_ltm`, `proposed_amount_usd`; compute max; compare)

The ratio predicates follow a **shared shape**: read threshold from state_predicate, read a specific ratio from event_instance, apply a comparison operator. The only per-branch variation is *which ratio to read*. Graph-native alternative: add a `world_state_attribute_name` attribute on `state_predicate` (e.g., `"ratio_snapshot_first_lien_net_leverage"`) and a typed `evaluation_pattern` attribute (`at_or_below` | `above` | `compound_greater_of` | `boolean_flag`). `predicate_holds` then dispatches on `evaluation_pattern` (4 branches instead of 7) and reads the per-instance attribute name.

**Why this is a concern, not a restructure:** the boolean and compound patterns don't share shape with the ratio pattern; you still need multiple branches. The saving is incremental (7 → 4 branches), and adding a new ratio-type predicate today means editing the function text — tomorrow it would mean adding a seed row. That's worth doing eventually but not before Prompt 07.

**Park.** Flag revisits when a new covenant adds a 5th ratio type or a compound predicate.

### `child_count($cond: condition) -> integer`

**Verdict:** `graph-native`.

Simple query + count reduce. No classification baked in.

### `holding_atomic_child_count($cond: condition, $ws: event_instance) -> integer`

**Verdict:** `graph-native`.

Chains through `condition_has_child` + `condition_references_predicate`, calls `predicate_holds` on atomic leaves. Nothing hardcoded.

### `condition_holds($cond: condition, $ws: event_instance) -> boolean`

**Verdict:** `graph-native`.

Dispatches on `condition_operator` ∈ {atomic, or, and}. The dispatch **is** graph-native — `condition_operator` is a typed attribute read at query time, not a string baked into logic. The three branches implement depth-bounded evaluation per the architecture's pilot limit.

Note: Part 4's `condition_topology` attribute is redundant with the dispatch in `condition_holds` (topology encodes the same structure the dispatch already walks). Topology is useful for the classification harness (Part 4's driver) but doesn't need to feed `condition_holds`. **No change.**

---

## `deontic_norm_functions.tql`

### `norm_is_structurally_complete($n: norm) -> boolean`

**Verdict:** `minor-concern`.

Reads six attributes + one role-playing check. Result is deterministic given the norm — does not depend on `$ws`.

Graph-native alternative: cache as `is_structurally_complete: boolean` on norm, populated at projection/load time. Queries then read the attribute instead of recomputing.

**Why this is a concern, not a restructure:** the computation is cheap, and centralising the completeness logic in one function (versus scattering "set this attr correctly" across N projection paths) is arguably cleaner. Caching would speed up the A1 check on large norm sets but the pilot has 63 norms.

**Park.**

### `norm_is_defeated($n: norm, $ws: event_instance) -> boolean`

**Verdict:** `graph-native`.

Walks `defeats` + `defeater_has_condition` relations and delegates to `condition_holds`. Composition, no hardcoded logic.

### `norm_is_in_force($n: norm, $ws: event_instance) -> boolean`

**Verdict:** `graph-native`.

Composes `norm_is_defeated` + `condition_holds`. No classification in function text.

### `applicable_permissions` / `applicable_prohibitions(...)` → `{ norm }`

**Verdict:** `graph-native`.

Attribute-driven filtering over `modality`, `norm_binds_subject.subject.party_role`, `norm_scopes_action.action.action_class_label`, with object-scope OR-branching (has matching object OR has no object scope). Everything reads graph data; the filter predicates are structural.

---

## `deontic_capacity_functions.tql`

### `additive_capacity(...) -> double`

**Verdict:** `graph-native`. Filters applicable permissions by `capacity_composition == "additive"` and sums `cap_usd`. No hardcoded classification.

### `categorical_capacities(...) -> { norm, cap_usd }`

**Verdict:** `graph-native`. Same filter-by-attribute pattern, returns stream.

### `has_unlimited_conditional_capacity(...) -> boolean`

**Verdict:** `graph-native`. Exists-check for `capacity_composition == "unlimited_on_condition"` among applicable permissions.

### `add_contributors_sum($pool: norm, $ws: event_instance) -> double`

**Verdict:** `graph-native`. Filters `norm_contributes_to_capacity` edges by `aggregation_function == "sum"` and `aggregation_direction == "add"`, sums cap_usd. Part 2's `aggregation_direction` attribute on the edge made this possible.

### `subtract_contributors_sum(...)` → `double`

**Verdict:** `graph-native`. Sibling of above, filters `aggregation_direction == "subtract"`.

### `computed_from_sources_capacity_sum(...)` → `double`

**Verdict:** `graph-native`. Composes add + subtract helpers. Returns `add - sub`. No classification in function text.

### `computed_from_sources_capacity_greatest_of(...)` → `double`

**Verdict:** `graph-native`. Filters contributors by `aggregation_function == "greatest_of"` and `aggregation_direction == "add"`, reduces via `max($c)`. Rejects malformed subtract-direction edges at query time.

### `reallocated_capacity_to($target_action_label, ...)` → `double`

**Verdict:** `stub`. Returns 0. Parameter-use guards reference args so TypeDB accepts the signature. Replace when Prompt 07 projects v3's `basket_reallocates_to` into `norm_contributes_to_capacity` bridges.

---

## `deontic_pathway_functions.tql`

### `norm_enables_hop($action_label, $from_state, $to_state, $ws) -> boolean`

**Verdict:** `needs-restructure`.

Current implementation: applicable_permissions with `$to_state` as the object_label. This treats the "to state" as an object_class, which is a pilot shortcut. The real semantic is **state transition** — the norm takes the world from state A to state B, where A and B are states of the world, not objects.

Graph-native alternative: a typed `state_transition` relation with roles `from_state` and `to_state` (both state_predicate instances) and a `transitioned_by` role to the norm. Pathway evaluation becomes a reachability walk over this relation. Prompt 06 (earlier) noted this for later.

**Defer until Prompt 07 models pathway state transitions.** This is unconsumed in the current pilot since no Duck Creek gold question traverses a hop pathway.

### `state_reachable($from_state, $to_state, $ws, $max_hops) -> boolean`

**Verdict:** `needs-restructure`.

Same story — matches on `object_class_label` as a stand-in for state identity. Should traverse typed state_transition edges.

**Defer until Prompt 07.**

---

## `deontic_validation_functions.tql`

### `norm_has_required_fields($n: norm) -> boolean`

**Verdict:** `graph-native`. Attribute existence checks. No classification baked in.

### `norm_has_scope($n: norm) -> boolean`

**Verdict:** `graph-native`. Relation existence check.

### `covenant_missing_expected_norm_kinds($covenant, $deal_id) -> { modality }`

**Verdict:** `stub`. Returns empty stream via sentinel. Real implementation diffs against the `expected_norm_kind` seed + deal's norm set. Defer — the shape is fine, body fills in once Prompt 07 emits deal-tagged norms.

---

## `deontic_pattern_functions.tql`

### `has_unlimited_conditional_without_cap($deal_id) -> boolean`

**Verdict:** `graph-native`. Filters on `capacity_composition == "unlimited_on_condition"` + absence of `cap_usd`. Attribute-driven.

### `has_exception_defeating_critical_prohibition($deal_id) -> boolean`

**Verdict:** `minor-concern`.

Body hardcodes the "critical" action-class set: `transfer_material_intellectual_property`, `make_dividend_payment`. If the set grows to five or more, the inline disjunction becomes unwieldy and editing the function text each time is painful.

Graph-native alternative: add a `is_critical_action` boolean attribute on `action_class` (or an `action_class_criticality` enum). Patterns query by filter.

**Park.** Two-element hardcoded list is a small cost; revisit if the set grows.

### `has_undefined_reference_term($deal_id) -> boolean`

**Verdict:** `stub`. Always false via sentinel. Real implementation cross-references norm `source_text` substrings against v3's `ip_definition` / `transfer_definition` / `materiality_definition` entities. Defer to Prompt 07 (projection).

---

## Aggregate findings

| Verdict | Count | Functions |
|---|---|---|
| `graph-native` | 17 | `child_count`, `holding_atomic_child_count`, `condition_holds`, `norm_is_defeated`, `norm_is_in_force`, `applicable_permissions`, `applicable_prohibitions`, `additive_capacity`, `categorical_capacities`, `has_unlimited_conditional_capacity`, `add_contributors_sum`, `subtract_contributors_sum`, `computed_from_sources_capacity_sum`, `computed_from_sources_capacity_greatest_of`, `norm_has_required_fields`, `norm_has_scope`, `has_unlimited_conditional_without_cap` |
| `minor-concern` | 3 | `predicate_holds` (per-label dispatch hardcodes attribute reads), `norm_is_structurally_complete` (cache candidate), `has_exception_defeating_critical_prohibition` (hardcoded "critical" set) |
| `needs-restructure` | 2 | `norm_enables_hop`, `state_reachable` (both need typed state_transition relation) |
| `stub` | 3 | `reallocated_capacity_to`, `covenant_missing_expected_norm_kinds`, `has_undefined_reference_term` |

**Total:** 25 functions. 17 pass cleanly. 3 are worth restructuring eventually but not before Prompt 07. 2 require Prompt-07-era structural work (pathway state transitions). 3 are stubs awaiting projection.

## Pre-Prompt-07 action items

**None** from this audit. The three `minor-concern` items are park-worthy. The two `needs-restructure` items are already deferred to Prompt 07's pathway work. The three `stub` items are scheduled to fill once projection runs.

Park-worthy items to revisit post-pilot:

1. `predicate_holds` dispatch by `evaluation_pattern` attribute + `world_state_attribute_name` (graph-native per-predicate evaluation config).
2. `norm_is_structurally_complete` cached as `is_structurally_complete` attribute on norm.
3. `has_exception_defeating_critical_prohibition` driven by `action_class.is_critical` attribute.
