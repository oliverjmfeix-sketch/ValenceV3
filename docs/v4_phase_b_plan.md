# Phase B — Data model additions (revised)

> Revised against worktree state after pre-execution review found 6
> errors in the original plan. See `docs/v4_phase_b_plan_review_notes.md`
> for the diagnosis. Original plan structure preserved; corrections
> inlined.

Adds three structural capabilities the failure audit identified as missing:
temporal anchors, reallocation relations, and cross-covenant proceed-flow
relations. Plus an object-scope granularity audit.

Each substantive commit is a complete vertical slice — schema addition, GT
authoring, projection update, verification.

Prerequisites: Phase A (norm_id rename) has landed. All references to norms
assume the post-Phase-A categorical identifiers.

No re-extraction. No Claude SDK calls. Preserve the $12.95 Duck Creek RP
extraction.

**Scope clarification:** Phase B addresses the schema-shape failures from
Q1/Q2/Q3/Q4. Q5 (calculated $520m figure) and Q6 (INCONCLUSIVE feasibility)
are evaluation-layer concerns — out of scope.

Four commits.

---

## Standing instructions

- TypeDB Cloud, not local. `.env` at `C:/Users/olive/ValenceV3/.env`.
- Read files when live query unavailable.
- **Probe schema additions against `valence_v4_ground_truth`** (recreated
  each run via `--force`). No separate `valence_v4_throwaway` is configured.
- Domain logic in the graph, not Python.
- No re-extraction. Preserve `valence_v4` extracted state.
- Rule 8.1 — world state per-query input, never stored.
- Branch local-only. No push.
- Read `docs/v4_foundational_rules.md`, `docs/v4_deontic_architecture.md`
  §4, `docs/v4_known_gaps.md`, `docs/v4_norm_id_rename_map.md`,
  `docs/typedb_patterns.md` (Pattern #14 + role-collision note) before
  starting.

## Schema additivity discipline

Schema additions in TypeDB 3.x are additive only. New attributes, new
relations, new entity types — fine in schema-mode transactions. Removing
or renaming requires DB rebuild (out of scope).

Apply via `app/scripts/init_schema_v4.py` (preflight-locked to
`valence_v4`; preserves extracted v3 entities via `--preserve-extraction`).
For `valence_v4_ground_truth`, use `app/scripts/load_ground_truth.py
--force` (drops + recreates).

## Pattern #14 reminder

Any query reading attributes off a relation must use the `links` form:
```tql
match
    $r isa <reltype>, links (role: $var, ...);
    try { $r has attr $v; };
```
Pattern #14 lives in `docs/typedb_patterns.md`.

## Role-name discipline

`receiver`, `source`, `event`, `target_norm` — verify no collision with
existing role names before declaring. Existing collision pattern on
`condition` is documented in known-gaps. **Up-front disambiguation
preferred** to avoid Pattern #14-style inference traps:

- Reallocation: `reallocation_receiver`, `reallocation_source`
- Cross-covenant: `governed_event`, `governed_norm`,
  `proceeds_event`, `proceeds_target_norm`

Document Pattern #15 in `typedb_patterns.md` if any new collision class
surfaces during execution.

---

## Commit 1 — Temporal anchors

### What's missing

Q1 gold answer: "All tests start growing from the first day of the fiscal
quarter in which the Closing Date occurs." No attribute on `norm` records
this. Builder sources need a temporal anchor and reference-period kind.

### Schema addition

Two optional string-enum attributes on `norm`:

- `growth_start_anchor` — when capacity starts accumulating:
  - `closing_date`
  - `closing_date_fiscal_quarter_start`
  - `first_test_period_after_closing`
  - `at_or_after_qualified_ipo`
  - `not_applicable`

- `reference_period_kind` — what time window the source measures:
  - `cumulative_since_anchor`
  - `ltm_at_test_date`
  - `point_in_time`
  - `not_applicable`

Decision: explicit `not_applicable` rather than absence — deterministic
queries.

```tql
norm,
    owns growth_start_anchor,
    owns reference_period_kind;

attribute growth_start_anchor, value string;
attribute reference_period_kind, value string;
```

### GT authoring

Author both attributes for every norm in
`app/data/duck_creek_rp_ground_truth.yaml`. Substantive cases:

- Builder sources (cumulative builder amount + sub-clauses):
  `growth_start_anchor: closing_date_fiscal_quarter_start`,
  `reference_period_kind: cumulative_since_anchor`
- **Sweep tiers (asset-sale net-leverage gates):**
  `reference_period_kind: ltm_at_test_date` (Net Leverage Ratio is LTM),
  `growth_start_anchor: not_applicable`. _[corrected from original plan]_
- Ratio basket: `reference_period_kind: ltm_at_test_date` (leverage ratio
  test), `growth_start_anchor: not_applicable`.
- Most other RP baskets: `not_applicable` for both.

Note: norm ID for builder retained-asset-sale source is
`6e76ed06_builder_source_retained_asset_sale_proceeds` (with `_proceeds`).

### Projection update

`app/services/deontic_projection.py` — emit the two attributes during
norm projection. Where projection has the information, use the same
values as GT; where it doesn't, emit `not_applicable` and flag in
known-gaps.

### Verification

```bash
# Apply schema additions to valence_v4 (additive)
"C:/Users/olive/ValenceV3/.venv/Scripts/python.exe" -m app.scripts.init_schema_v4 --preserve-extraction

# Reseed valence_v4_ground_truth (drops + recreates with new attributes)
"C:/Users/olive/ValenceV3/.venv/Scripts/python.exe" -m app.scripts.load_ground_truth --force

# Re-project valence_v4 norms with new attributes
"C:/Users/olive/ValenceV3/.venv/Scripts/python.exe" -m app.services.deontic_projection --deal 6e76ed06
```

Sanity query:
```tql
match
    $n isa norm,
        has norm_id $nid,
        has growth_start_anchor $gsa,
        has reference_period_kind $rpk;
select $nid, $gsa, $rpk;
```

Run validation harness; expect dict output `{"missing": 45, "spurious":
6, "mismatched": 0}` preserved.

```bash
"C:/Users/olive/ValenceV3/.venv/Scripts/python.exe" -m app.services.validation_harness --deal 6e76ed06
```

---

## Commit 2 — Reallocation relations

### What's missing

Q3 gold answer (verbatim): "under 6.06(j) amount available for Restricted
Debt Payment under 6.09(a) and amounts available for Investments under
6.03(y) can be reallocated to the making of Dividends."

Currently GT YAML stores reallocation as `reallocates_to: <norm_id>` on
the *source* norm — one-way attribute, source-keyed. Two existing edges:

- `6e76ed06_general_rdp_basket_permission.reallocates_to: 6e76ed06_general_rp_basket_permission`
- `6e76ed06_general_investment_basket_permission.reallocates_to: 6e76ed06_general_rp_basket_permission`

Plan: introduce relation `norm_reallocates_capacity_from`, migrate GT
authoring to receiver-keyed `reallocates_from:` blocks, drop legacy
`reallocates_to:` attribute.

### Pre-flight verification

Before authoring schema, query v3 `valence` DB for actual
`basket_reallocates_to` instances on Duck Creek. If zero, projection has
no v3 data to consume — Commit 2 becomes GT-only and projection emits
empty for v4 (flag in known-gaps).

### Schema addition

```tql
relation norm_reallocates_capacity_from,
    relates reallocation_receiver,
    relates reallocation_source,
    owns reallocation_mechanism,
    owns reduction_direction;

norm plays norm_reallocates_capacity_from:reallocation_receiver;
norm plays norm_reallocates_capacity_from:reallocation_source;

attribute reallocation_mechanism, value string;
attribute reduction_direction, value string;
```

`reallocation_mechanism` enum:
- `shares_pool` — single capacity pool; using one reduces the other
- `separate_pool` — separate caps but unused source-capacity borrowable
- `fungible_substitution` — receiver uses source's basket type
  interchangeably

`reduction_direction` enum:
- `bidirectional` / `receiver_only` / `source_only`

### GT authoring

Migrate the two existing source-keyed `reallocates_to:` attributes to
receiver-keyed blocks on `6e76ed06_general_rp_basket_permission`:

```yaml
reallocates_from:
  - source_norm_id: 6e76ed06_general_rdp_basket_permission
    reallocation_mechanism: <read source text>
    reduction_direction: <read source text>
  - source_norm_id: 6e76ed06_general_investment_basket_permission
    reallocation_mechanism: <read>
    reduction_direction: <read>
```

Remove the legacy `reallocates_to:` attribute from the two source norms.

Read source_text on each to determine mechanism + direction (don't guess).
Walk the YAML for any other reallocation language; add edges as
discovered.

### Projection update

Add `_project_reallocations` step to `deontic_projection.py`. Read v3
`basket_reallocates_to` edges (if present per pre-flight); emit
`norm_reallocates_capacity_from` edges in v4. If v3 data absent, emit
nothing and flag.

### Verification

```bash
# Schema additions
"C:/Users/olive/ValenceV3/.venv/Scripts/python.exe" -m app.scripts.init_schema_v4 --preserve-extraction

# GT reseed (picks up new attribute migration)
"C:/Users/olive/ValenceV3/.venv/Scripts/python.exe" -m app.scripts.load_ground_truth --force

# Projection
"C:/Users/olive/ValenceV3/.venv/Scripts/python.exe" -m app.services.deontic_projection --deal 6e76ed06
```

Query GT for reallocation edges:
```tql
match
    $r isa norm_reallocates_capacity_from,
        links (reallocation_receiver: $rec, reallocation_source: $src);
    $rec has norm_id $rid;
    $src has norm_id $sid;
select $rid, $sid;
```

Expected: at least 2 edges (6.06(j) → 6.09(a) and 6.06(j) → 6.03(y)).

A6 invariants may need extension; if so, update
`app/services/validation_harness.py`.

---

## Commit 3 — Cross-covenant proceeds-flow

### What's missing

Q4 gold answer chains asset-sale proceeds through Section 2.10(c)(iv)
prepay exemptions, sweep-tier retention (5.75x / 5.50x leverage), 6.05(z)
unlimited basket, de minimis thresholds ($20M/15% individual, $40M/30%
annual), into the Cumulative Amount via clause (f) (retained asset sale
source).

### Scope decision

Asset-sale source norms (`6e76ed06_asset_sale_*`) are **not authored** in
current GT. Authoring them pulls more covenant modeling into Phase B than
the original plan suggested.

**Scope this commit to `event_provides_proceeds_to_norm`** edges only —
those target existing builder-source norms. Defer
`event_governed_by_norm` until asset-sale source norms are authored
(future prompt).

### Schema additions

`event_class` entity (categorical, deal-agnostic):

```tql
entity event_class,
    owns event_class_id @key,
    owns event_class_label,
    owns event_class_description;

attribute event_class_id, value string;
attribute event_class_label, value string;
attribute event_class_description, value string;
```

Pilot scope: seed only `asset_sale_event`. Other event classes added
when consumers need them.

Disambiguated role names (no `event` collision with `event_targets_party`
or `event_targets_instrument`):

```tql
relation event_provides_proceeds_to_norm,
    relates proceeds_event,
    relates proceeds_target_norm,
    owns proceeds_flow_kind,
    owns proceeds_flow_conditions;

event_class plays event_provides_proceeds_to_norm:proceeds_event;
norm plays event_provides_proceeds_to_norm:proceeds_target_norm;

attribute proceeds_flow_kind, value string;
attribute proceeds_flow_conditions, value string;
```

`proceeds_flow_kind` enum:
- `retained_after_sweep`
- `declined_lender_payment`
- `excluded_below_threshold`

`proceeds_flow_conditions` — free-text for pilot; structured tree
post-pilot.

### GT authoring

Seed `asset_sale_event` event_class instance.

`asset_sale_event` provides proceeds to:
- `6e76ed06_builder_source_retained_asset_sale_proceeds` —
  `proceeds_flow_kind: retained_after_sweep`,
  `proceeds_flow_conditions: "if sweep tier exemption applies (Net
  Leverage Ratio ≤ 5.75x or ≤ 5.50x) or if proceeds below de minimis
  ($20M/15% individual, $40M/30% annual)"`
- `6e76ed06_builder_source_declined_asset_sale_proceeds` (verify exact
  ID) — `proceeds_flow_kind: declined_lender_payment`,
  `proceeds_flow_conditions: "if lender declines mandatory prepayment
  offer per Section 2.10"`

### Projection

Hardcoded mapping in `deontic_projection.py` for `asset_sale_event` for
pilot. Post-pilot: extraction questions surface this directly.

### Verification

```bash
"C:/Users/olive/ValenceV3/.venv/Scripts/python.exe" -m app.scripts.init_schema_v4 --preserve-extraction
"C:/Users/olive/ValenceV3/.venv/Scripts/python.exe" -m app.scripts.load_ground_truth --force
"C:/Users/olive/ValenceV3/.venv/Scripts/python.exe" -m app.services.deontic_projection --deal 6e76ed06
```

Query:
```tql
match
    $e isa event_class, has event_class_id $eid;
    $r isa event_provides_proceeds_to_norm,
        links (proceeds_event: $e, proceeds_target_norm: $tn);
    $tn has norm_id $tid;
select $eid, $tid;
```

Expected: at least 2 rows for `asset_sale_event` (retained + declined
builder source clauses).

---

## Commit 4 — Object scope audit + final verification

### What to check

`object_class` and `unrestricted_sub_equity` are already in schema (line
166 of schema_v4_deontic.tql). Q2's failure is parser-side (intent-layer
object-scope extraction), not schema-side.

Live path: audit GT for `norm_scopes_object` edges on the 6.06(p) norm
referenced by Q2. If absent, add the edge.

```tql
match
    $n isa norm, has norm_id "6e76ed06_unrestricted_sub_equity_distribution_permission";
    $r isa norm_scopes_object, links (norm: $n, object: $o);
    $o isa $type;
select $type;
```

If no `norm_scopes_object` edges to `unrestricted_sub_equity` /
`unrestricted_subsidiary_equity_or_assets`, add to GT YAML.

### Final verification

```bash
"C:/Users/olive/ValenceV3/.venv/Scripts/python.exe" -m app.services.validation_harness --deal 6e76ed06
"C:/Users/olive/ValenceV3/.venv/Scripts/python.exe" -m app.services.classification_measurement --deal 6e76ed06 --field all --prompt-version v2
```

Expected:
- A1 pass
- A4 round-trip — preserved or shifted explainably (new relations may
  introduce missing/spurious deltas)
- A5 pass
- A6 pass (with new invariants if added)

Schema audit query counts:
```tql
match $n isa norm, has growth_start_anchor $gsa; reduce $count = count;
match $r isa norm_reallocates_capacity_from; reduce $count = count;
match $r isa event_provides_proceeds_to_norm; reduce $count = count;
```

### Documentation updates

- `docs/typedb_patterns.md` — Pattern #15+ if surfaced (especially around
  role-name disambiguation under Pattern #14)
- `docs/v4_known_gaps.md` — flag remaining items:
  - reallocation_mechanism / reduction_direction values may need Phase C
    re-extraction if v3 doesn't capture them
  - proceeds_flow_conditions is free-text; structured tree post-pilot
  - Other event classes added when consumers need them
  - `event_governed_by_norm` deferred until asset-sale source norms
    authored

---

## Commit hashes

(Populated during execution.)

| Commit | SHA |
|---|---|
| 1 — temporal anchors | TBD |
| 2 — reallocation | TBD |
| 3 — cross-covenant proceeds-flow | TBD |
| 4 — object scope + verify | TBD |
