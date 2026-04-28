# Phase C design — projection rules as graph SSoT

## Purpose

Designs the v4 schema for projection rules. Resolves what entities,
relations, and attributes are needed so v3-to-v4 projection becomes a
graph query instead of Python decision logic.

The design target: **all domain content lives as typed graph entities
and attributes. Python at projection time performs typed dispatch on
enum-valued attributes by mechanical mapping.** This is a mechanical
interpreter pattern: the codes (`comparison_operator`,
`arithmetic_operator`, `target_topology`, `assigned_role_name`,
`emits_relation_type`) are domain content defined by the schema's enum
vocabulary; interpreting them is uniform across every rule.

What is eliminated:

- Domain-specific dispatch (no `if entity.type == "rp_basket": handle_rp_basket()`)
- Embedded query strings (no `query = f"match $x has cap_usd > {threshold}; ..."`)
- JSON-in-attribute (no `payload: '{"target": "..."}'`)
- Template syntax (no Jinja-style placeholders)

What is not eliminated: the executor's mini-interpreter for the schema's
enum vocabulary. This is the smallest acceptable Python role.

This document is the spec. Phase C's commits implement and migrate to it.

## Scope of what projection does

Projection rules express the v3-to-v4 mapping for **clean v3 data only**.
v3 data-quality issues — mixed fraction/percentage encoding for
`cap_grower_pct`, alternative attribute names like
`cap_usd`/`basket_amount_usd`/`annual_cap_usd`, categorical
disambiguation lookup tables — are NOT projection-rule concerns. They
are extraction-side post-processing concerns. See "Heuristics handling"
below.

Three operation categories in projection scope:

1. **Match** a v3 entity (typed criteria)
2. **Emit** v4 entities (norm, defeater, condition-tree node) with
   attributes resolved from clean v3 attributes via typed value sources
3. **Emit relations** with role assignments resolved structurally

Each category becomes a structured part of the schema below.

## Heuristics handling

The existing `deontic_projection.py` carries v3-data-quality fixups —
scale coercion (`if cap_grower <= 5.0: cap_grower *= 100.0`), chained-OR
fallbacks for alternative attribute names, etc. These don't belong in
projection rules.

**Phase C Commit 0 has two parts:**

**(a) Move heuristics from `deontic_projection.py` to `app/services/extraction.py`** as a post-extraction normalization step. After extraction
completes (and before any consumer reads the v3 entities), the
normalization pass runs and writes back canonicalized values:

- `cap_grower_pct` fraction-to-percentage normalization
- Canonical `cap_usd` chosen from `cap_usd` / `basket_amount_usd` /
  `annual_cap_usd` (preserving precedence)
- Categorical disambiguation tables (the `_BUILDER_OTHER_DISAMBIGUATOR`
  pattern) become extraction-side mapping if applicable

Future extractions normalize automatically. Projection consumes a clean
v3 dataset.

**(b) Apply the same normalization to existing `valence_v4` data** as a
one-time fixup transaction. Walk the existing `rp_basket`,
`builder_basket`, etc. entities; rewrite `cap_grower_pct` values where
they are fractional; canonicalize `cap_usd`. Each modified entity gets
a `cleaned_by_phase_c_commit_0` marker attribute for audit trail.

Pre-fixup schema snapshot + entity dump preserved on disk for rollback.

**After Commit 0:** future extractions normalize automatically; existing
data is clean; projection has no v3-quality concerns. Phase C does not
depend on Phase D. Phase D can later improve extraction prompts so the
post-processing step is unnecessary, at which point the normalization
function in `extraction.py` is dead code.

## Schema design

### Top-level: the rule entity

```tql
entity projection_rule,
    owns projection_rule_id @key,
    owns projection_rule_label,
    owns projection_rule_description;

attribute projection_rule_id, value string;
attribute projection_rule_label, value string;
attribute projection_rule_description, value string;
```

A rule is identified, labeled, described. No content of the rule lives
on the rule entity itself — match conditions, target entity, and
relation emissions are all reached via relations to other typed
entities.

### Match criteria

Match criteria are typed. Different criterion subtypes express different
match conditions:

```tql
entity match_criterion @abstract;

entity entity_type_criterion sub match_criterion,
    owns matches_v3_entity_type;

entity subtype_criterion sub match_criterion,
    owns matches_v3_subtype;

entity attribute_value_criterion sub match_criterion,
    owns checks_v3_attribute_name,
    owns comparison_operator;
    # plus relation to a value_source for the comparison value

entity attribute_existence_criterion sub match_criterion,
    owns checks_v3_attribute_name,
    owns existence_check;

entity linked_via_relation_criterion sub match_criterion,
    owns matches_via_v3_relation_type,
    owns matches_via_v3_role;
    # plus criterion_requires_linked_match to a sub-criterion

# Explicit AND/OR composition (added per Q1 review)
entity match_criterion_group sub match_criterion,
    owns group_combinator;          # "and" | "or"

attribute matches_v3_entity_type, value string;
attribute matches_v3_subtype, value string;
attribute checks_v3_attribute_name, value string;
attribute comparison_operator, value string;     # equals | in | greater_than | ...
attribute existence_check, value string;          # exists | not_exists
attribute matches_via_v3_relation_type, value string;
attribute matches_via_v3_role, value string;
attribute group_combinator, value string;
```

A rule has match criteria via `rule_has_match_criterion`:

```tql
relation rule_has_match_criterion,
    relates owning_rule,
    relates applied_criterion;
projection_rule plays rule_has_match_criterion:owning_rule;
match_criterion plays rule_has_match_criterion:applied_criterion;
```

A rule with multiple top-level criterion edges matches v3 entities
satisfying all of them (implicit AND). For OR within a rule, use a
`match_criterion_group` with `group_combinator: "or"`:

```tql
relation criterion_group_has_member,
    relates parent_group,
    relates member_criterion;
match_criterion_group plays criterion_group_has_member:parent_group;
match_criterion plays criterion_group_has_member:member_criterion;
```

`linked_via_relation_criterion` references another `match_criterion`:

```tql
relation criterion_requires_linked_match,
    relates parent_criterion,
    relates linked_criterion;
linked_via_relation_criterion plays criterion_requires_linked_match:parent_criterion;
match_criterion plays criterion_requires_linked_match:linked_criterion;
```

For `attribute_value_criterion`, the comparison value is a value_source:

```tql
relation criterion_uses_comparison_value,
    relates owning_criterion,
    relates comparison_value_source;
attribute_value_criterion plays criterion_uses_comparison_value:owning_criterion;
value_source plays criterion_uses_comparison_value:comparison_value_source;
```

### Value sources

Whenever a value is needed (criterion comparison, emitted attribute,
edge attribute, role-filler lookup), it is reached via a `value_source`.

```tql
entity value_source @abstract;

entity literal_string_value_source sub value_source,
    owns literal_string_value;
entity literal_double_value_source sub value_source,
    owns literal_double_value;
entity literal_long_value_source sub value_source,
    owns literal_long_value;
entity literal_boolean_value_source sub value_source,
    owns literal_boolean_value;

entity v3_attribute_value_source sub value_source,
    owns reads_v3_attribute_name;
    # may have a fallback default via value_source_has_default

entity deal_id_value_source sub value_source;
    # yields the deal_id of the projection context

entity concatenation_value_source sub value_source;
    # ordered children via concatenation_has_ordered_part

entity arithmetic_value_source sub value_source,
    owns arithmetic_operator;       # add | subtract | multiply | divide

entity conditional_value_source sub value_source;
    # boolean test + then-branch + else-branch (relations below)

entity produced_norm_id_value_source sub value_source;
    # references a norm_template another rule will emit

attribute literal_string_value, value string;
attribute literal_double_value, value double;
attribute literal_long_value, value long;
attribute literal_boolean_value, value boolean;
attribute reads_v3_attribute_name, value string;
attribute arithmetic_operator, value string;
```

Composition relations:

```tql
relation value_source_has_default,
    relates primary_source,
    relates default_source;
v3_attribute_value_source plays value_source_has_default:primary_source;
value_source plays value_source_has_default:default_source;

relation concatenation_has_ordered_part,
    relates owning_concatenation,
    relates concatenation_part,
    owns sequence_index;
concatenation_value_source plays concatenation_has_ordered_part:owning_concatenation;
value_source plays concatenation_has_ordered_part:concatenation_part;
attribute sequence_index, value long;

relation arithmetic_has_left_operand,
    relates owning_expression,
    relates left_operand;
relation arithmetic_has_right_operand,
    relates owning_expression,
    relates right_operand;
arithmetic_value_source plays arithmetic_has_left_operand:owning_expression;
arithmetic_value_source plays arithmetic_has_right_operand:owning_expression;
value_source plays arithmetic_has_left_operand:left_operand;
value_source plays arithmetic_has_right_operand:right_operand;

relation conditional_has_test,
    relates owning_conditional,
    relates test_value_source;
relation conditional_has_then_branch,
    relates owning_conditional,
    relates then_value_source;
relation conditional_has_else_branch,
    relates owning_conditional,
    relates else_value_source;
conditional_value_source plays conditional_has_test:owning_conditional;
conditional_value_source plays conditional_has_then_branch:owning_conditional;
conditional_value_source plays conditional_has_else_branch:owning_conditional;
value_source plays conditional_has_test:test_value_source;
value_source plays conditional_has_then_branch:then_value_source;
value_source plays conditional_has_else_branch:else_value_source;

relation produced_norm_reference,
    relates referencing_source,
    relates referenced_template;
produced_norm_id_value_source plays produced_norm_reference:referencing_source;
norm_template plays produced_norm_reference:referenced_template;
```

**Conditional typing:** runtime check (not subtype-per-output-type per
Q2). The executor verifies test yields boolean and both branches yield
the conditional's expected output type at resolution time. Authoring
errors surface as runtime errors during projection, not at insert time.

### Norm templates

```tql
entity norm_template,
    owns norm_template_id @key,
    owns norm_template_label;

attribute norm_template_id, value string;
attribute norm_template_label, value string;

relation rule_produces_norm_template,
    relates owning_rule,
    relates produced_template;
projection_rule plays rule_produces_norm_template:owning_rule;
norm_template plays rule_produces_norm_template:produced_template;
```

A norm template emits attributes via `attribute_emission`:

```tql
entity attribute_emission,
    owns emitted_attribute_name;
attribute emitted_attribute_name, value string;

relation template_emits_attribute,
    relates emitting_template,
    relates emitted_attribute;
norm_template plays template_emits_attribute:emitting_template;
attribute_emission plays template_emits_attribute:emitted_attribute;

relation attribute_emission_uses_value,
    relates owning_emission,
    relates source_value;
attribute_emission plays attribute_emission_uses_value:owning_emission;
value_source plays attribute_emission_uses_value:source_value;
```

To express "emit a norm with `cap_usd` from v3 attribute
`general_rp_basket_amount`":

- norm_template
  - attribute_emission (`emitted_attribute_name: "cap_usd"`)
    - value_source: v3_attribute_value_source (`reads_v3_attribute_name: "general_rp_basket_amount"`)

### Relation templates

A norm template emits zero or more relation templates:

```tql
entity relation_template,
    owns relation_template_id @key,
    owns emits_relation_type;

attribute relation_template_id, value string;
attribute emits_relation_type, value string;

relation template_emits_relation,
    relates emitting_template,
    relates emitted_relation;
norm_template plays template_emits_relation:emitting_template;
relation_template plays template_emits_relation:emitted_relation;
defeater_template plays template_emits_relation:emitting_template;
```

(Note: `template_emits_relation:emitting_template` shared by
`norm_template` and `defeater_template` — the role-name discipline
keeps it generic, no rename needed.)

```tql
entity role_assignment,
    owns assigned_role_name;
attribute assigned_role_name, value string;

relation relation_template_assigns_role,
    relates owning_relation_template,
    relates emitted_role_assignment;
relation_template plays relation_template_assigns_role:owning_relation_template;
role_assignment plays relation_template_assigns_role:emitted_role_assignment;
```

Role fillers — three options:

```tql
entity role_filler @abstract;

# (a) The norm being emitted by this template fills it
entity emitted_norm_role_filler sub role_filler;

# (b) Static lookup of an existing entity in the graph
entity static_lookup_role_filler sub role_filler,
    owns lookup_entity_type,
    owns lookup_attribute_name;

# (c) Cross-rule reference to another norm template's emission
entity produced_norm_role_filler sub role_filler;

attribute lookup_entity_type, value string;
attribute lookup_attribute_name, value string;

relation role_assignment_filled_by,
    relates owning_role_assignment,
    relates assignment_filler;
role_assignment plays role_assignment_filled_by:owning_role_assignment;
role_filler plays role_assignment_filled_by:assignment_filler;

relation static_lookup_uses_value,
    relates owning_filler,
    relates lookup_value_source;
static_lookup_role_filler plays static_lookup_uses_value:owning_filler;
value_source plays static_lookup_uses_value:lookup_value_source;

relation produced_norm_filler_references_template,
    relates referencing_filler,
    relates referenced_template;
produced_norm_role_filler plays produced_norm_filler_references_template:referencing_filler;
norm_template plays produced_norm_filler_references_template:referenced_template;
```

Edge attributes on emitted relations reuse `attribute_emission`:

```tql
relation relation_template_emits_edge_attribute,
    relates owning_relation_template,
    relates emitted_edge_attribute;
relation_template plays relation_template_emits_edge_attribute:owning_relation_template;
attribute_emission plays relation_template_emits_edge_attribute:emitted_edge_attribute;
```

### Condition templates

```tql
entity condition_template,
    owns condition_template_id @key,
    owns target_topology,
    owns target_operator;

attribute target_topology, value string;     # atomic | or | and (resolved at emit time)
attribute target_operator, value string;     # atomic | or | and

relation template_emits_root_condition,
    relates emitting_template,
    relates root_condition;
norm_template plays template_emits_root_condition:emitting_template;
defeater_template plays template_emits_root_condition:emitting_template;
condition_template plays template_emits_root_condition:root_condition;
```

Atomic condition templates reference shared `predicate_specifier`
entities (per Q4):

```tql
entity predicate_specifier,
    owns specifies_predicate_id;
attribute specifies_predicate_id, value string;

relation atomic_condition_references_predicate,
    relates owning_condition_template,
    relates referenced_specifier;
condition_template plays atomic_condition_references_predicate:owning_condition_template;
predicate_specifier plays atomic_condition_references_predicate:referenced_specifier;
```

Multiple condition_template entities can reference the same
predicate_specifier — sharing reduces entity bloat. A predicate
specifier may resolve dynamically via value_source (when threshold or
operator come from v3):

```tql
relation predicate_specifier_uses_value,
    relates owning_specifier,
    relates dynamic_value_source;
predicate_specifier plays predicate_specifier_uses_value:owning_specifier;
value_source plays predicate_specifier_uses_value:dynamic_value_source;
```

Compound condition templates have ordered children:

```tql
relation condition_template_has_child,
    relates parent_condition,
    relates child_condition,
    owns child_index;
condition_template plays condition_template_has_child:parent_condition;
condition_template plays condition_template_has_child:child_condition;
```

### Defeater templates

Same shape as norm templates. Defeater templates participate in
`template_emits_attribute`, `template_emits_relation`, and
`template_emits_root_condition` relations (all generic role names —
no defeater-specific roles needed):

```tql
entity defeater_template,
    owns defeater_template_id @key,
    owns defeater_template_label;

attribute defeater_template_id, value string;
attribute defeater_template_label, value string;

relation rule_produces_defeater_template,
    relates owning_rule,
    relates produced_template;
projection_rule plays rule_produces_defeater_template:owning_rule;
defeater_template plays rule_produces_defeater_template:produced_template;

defeater_template plays template_emits_attribute:emitting_template;
defeater_template plays template_emits_relation:emitting_template;
defeater_template plays template_emits_root_condition:emitting_template;
```

### Provenance

Every emitted v4 entity tracks its source rule:

```tql
relation produced_by_rule,
    relates produced_entity,
    relates owning_rule,
    relates triggering_v3_entity;
norm plays produced_by_rule:produced_entity;
defeater plays produced_by_rule:produced_entity;
condition plays produced_by_rule:produced_entity;
projection_rule plays produced_by_rule:owning_rule;
```

After projection, every v4 entity has exactly one `produced_by_rule`
edge identifying which rule emitted it and which v3 entity triggered
the emission. "Why does this norm have this kind?" becomes a graph
query.

## Two-pass executor architecture

[Per Q3 review.] Forward references resolved by two-pass projection,
not topological sort:

- **Pass 1:** Walk projection_rules; for each rule, run its match
  query against `valence`; for each match, emit norm/defeater entities
  with attributes resolved from value sources. Cross-rule references
  (e.g., norm A's relation needs norm B emitted by rule B') deferred
  via a `pending_reference_marker` attribute on the emitting entity.

- **Pass 2:** Walk all entities with `pending_reference_marker`;
  resolve the deferred references against post-pass-1 graph state;
  emit deferred relations; clear the marker.

Mutual references (A↔B) handled trivially. Topological sort would
deadlock on mutual references (which exist for builder b_aggregate ↔
sub-sources).

## Migration path: replace `deontic_mapping` via mechanical conversion

The existing `deontic_mapping` entities (15 in `rp_deontic_mappings.tql`)
plus `condition_builder_spec` entities (5 in `rp_condition_builders.tql`)
are a coarser-grained version of `projection_rule`. The new schema is a
strict refinement of the old vocabulary — there is a deterministic
mapping from each existing entry to a `projection_rule` subgraph.

Phase C replaces deontic_mapping in stages:

- Schema lands first
- A single rule is hand-authored to verify schema-expressibility
  (byte-identical output to Python projection)
- A mechanical converter then produces the full `projection_rule`
  subgraph from the existing deontic_mapping corpus
- Parallel run verifies parity
- Switchover routes `deontic_projection.py` through the new executor
- Old code paths deleted

If the converter struggles to express a deontic_mapping (or condition
builder), that is a schema gap surfaced before bulk authoring. The
converter IS the schema-sufficiency proof.

## Commit structure

| # | Title | Output |
|---|---|---|
| 0a | Move heuristics to extraction-side post-processing | `app/services/extraction.py` runs normalization after extraction completes. Heuristics deleted from `deontic_projection.py`. |
| 0b | One-time fixup of existing `valence_v4` data | Walk existing v3 entities; rewrite normalized values; mark each modified entity with `cleaned_by_phase_c_commit_0`. Pre-fixup snapshot preserved. |
| 1 | Schema | `projection_rule` + 25 entity types + 27 relations. Role-name discipline applied at authoring (Pattern #15). `match_criterion_group` and shared `predicate_specifier` baked in. |
| 1.5 | Pilot rule end-to-end | One smallest builder sub-source rule authored; new executor produces byte-identical output to (post-Commit-0) Python projection. **Hard gate:** Commit 2 cannot start until 1.5 passes. |
| 2 | Mechanical conversion | `deontic_mapping` → `projection_rule` converter. Run on the 15 existing mappings + 5 condition_builder specs. Output: full `projection_rule` subgraph in graph state. Converter struggle points = schema gaps surfaced. |
| 3 | Parallel run + benchmark gate | Both old + new executors emit to scratch DBs; structural diff = zero diff verifies parity. **Performance benchmark:** new vs old execution time. >10× regression triggers a denormalization pass before Commit 4. |
| 4 | Switchover | `deontic_projection.py` routes through new executor. Old `project_entity` deleted. |
| 5 | Deprecation + docs | Delete `deontic_mapping` consumption code. Retain seed `.tql` files for archive. Document the new schema in `docs/v4_deontic_architecture.md` §4. Update `docs/v4_known_gaps.md` with items surfaced during conversion. |

## Performance benchmark gate (Q6)

Per-rule entity volume: simple rule emitting a norm with ~8 attrs, 4
scope edges, 1 condition tree, 1 contributes-to relation expands to
~50-80 graph entities. Across 15 existing mappings, expected total:
~750-1100 graph entities for the rule corpus alone.

**Benchmark gate at Commit 3:** new executor runtime vs current Python
projection runtime on the same Duck Creek deal. >10× regression
triggers denormalization (e.g., precomputed compiled query strings
cached as a `compiled_match_query` attribute on the rule, rebuilt on
rule update). Without the gate, scope is at risk if executor traversal
proves untractable.

## Estimated entity volume

24 concrete entity types (5 match_criterion subtypes including the new
`match_criterion_group`, 10 value_source subtypes, plus `projection_rule`,
`norm_template`, `attribute_emission`, `relation_template`,
`role_assignment`, 3 role_filler subtypes, `condition_template`,
`predicate_specifier`, `defeater_template`).

27 relations (added `criterion_group_has_member`).

~30 attributes.

This is the smallest schema that captures every projection operation
as a typed graph structure. Reductions reintroduce a compromise
(JSON-in-attribute, embedded query strings, Python decision logic).

## Open items

### Bundle refresh policy

Now that `v4-deontic` is on origin, the bundle in `transfers/` is
documented as "refresh-before-use fallback." Phase C does not add a
policy commit. Bundle refresh stays ad-hoc, on-demand only when a
no-network-access machine needs to bootstrap.

### Provenance for entities modified by Commit 0

Commit 0b modifies existing `valence_v4` v3 entities. They have no
`produced_by_rule` provenance (they were extracted, not projected).
Each modified entity gets a `cleaned_by_phase_c_commit_0` marker
attribute for audit trail.

```tql
attribute cleaned_by_phase_c_commit_0, value boolean;
rp_basket owns cleaned_by_phase_c_commit_0;
builder_basket owns cleaned_by_phase_c_commit_0;
# (etc. for each v3 entity type touched by the fixup)
```

### What if Commit 1.5 fails the byte-identical test?

The schema is provably insufficient for the smallest rule; the design
needs revision before Commit 2 can proceed. **Commit 2 cannot start
until 1.5 passes.** If 1.5 fails, surface the specific schema gap,
revise the schema in a Commit 1.x patch, re-run 1.5. No "press on and
patch later."

### Heuristics' future after Phase D

The extraction-side normalization function that lands in Commit 0a is
extant but provisional. Phase D's extraction-prompt improvements aim
to make the post-processing unnecessary by producing canonical values
directly. Once Phase D verifies clean extraction output across a
representative corpus, the normalization function can be deleted as
dead code. Phase C does not block on Phase D; Phase D does not block on
Phase C.

## What's no longer in Phase C scope

- v3 data-quality heuristics in projection rules (moved to Commit 0a
  extraction post-processing + Commit 0b one-time fixup)
- Mixed fraction/percentage normalization for any new attributes
- Alternative-attribute-name fallback chains
- Categorical disambiguation lookup tables (e.g.,
  `_BUILDER_OTHER_DISAMBIGUATOR`) — these are extraction-side concerns
  too, since they map v3 flag names to disambiguator strings

## What this phase does not do

- Does not modify Phase B's schema additions (temporal anchors,
  reallocation relations, event_class entity + proceeds-flow relation,
  object scope edits)
- Does not push to `main` — `v4-deontic` remains pre-production
- Does not run any Claude SDK calls
- Does not modify v3 extraction prompts (Phase D scope)
- Does not modify v4 extraction (no re-extraction; Duck Creek
  $12.95 artifact preserved)
