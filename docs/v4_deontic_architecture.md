# Valence v4 — Deontic Architecture Specification

> Status: design contract. Companion to [`v4_foundational_rules.md`](v4_foundational_rules.md). Every subsequent v4 development prompt references this document.

---

## 1. Purpose and scope

v4 implements a deontic knowledge graph model for Valence, starting with Restricted Payments (RP) as a pilot. The goal is to move legal reasoning out of synthesis-guidance prose and into queryable graph structure. Norms — permissions, prohibitions, obligations, powers, and exceptions — become first-class typed entities with explicit modality, subject, scope, condition, and source. Every substantive legal conclusion a user sees is reached by composing TypeDB-function calls over these typed norms, never by a prompt re-deriving the rule from text.

Scope of the first build is RP only. MFN, DI, Liens, Asset Sales, Debt Incurrence, Pro Forma, Builder Math, Investments, Intercompany, Prepayments, Amendments, Reporting, and Affiliate Transactions all remain v3 behavior or return a `v4 supports RP only` stub until the Duck Creek RP acceptance test passes (Rule 7.1). This document is the contract against which every v4 PR will be reviewed; its correctness is the prerequisite for all subsequent prompts.

---

## 2. Theoretical foundation

The deontic model draws from four traditions. The point of enumerating them is so that future maintainers know *why* the primitives are what they are and can extend the model without recreating prior debates.

### Hohfeldian analysis — atomic legal relations
Hohfeld showed that every complex legal situation decomposes into four correlative pairs: right/duty, privilege/no-right, power/liability, immunity/disability. v4 uses this as a decomposition rule: any credit-agreement provision that *looks* complex is broken into atomic norms with a single modality, subject, and scope each. "The Borrower may, subject to 6.06(o), make Restricted Payments up to the Cumulative Amount provided no EoD exists" is not one norm — it is a permission (privilege) held by the Borrower, correlated with the lenders' no-right to object, bounded by a ratio_condition and an event_of_default_condition.

### Standard Deontic Logic with defeasibility — modalities and override
SDL gives three modal operators (permitted P, forbidden F, obligatory O) with the standard duality O = ¬P¬. Classical SDL is brittle for law because it cannot express "rule R applies *unless* exception E" without making the whole system inconsistent. v4 uses the Nute/Prakken-style **defeasible** extension: a norm holds by default and can be defeated by an explicit `defeats` edge from an exception. Override is a graph structure, not a prompt rule.

### Anderson's reduction — violation consequents
Anderson reduced deontic operators to alethic ones plus a sanction predicate: *O(p)* ≡ *(¬p → V)* where V is "a violation has occurred." v4 keeps violation consequents explicit via `norm_has_violation_consequent`, which attaches the consequence (typically an event-of-default trigger, a mandatory prepayment, or a step-in right) to the norm it attaches to. This is why obligations model consequents; the absence of one is a schema error, not a default.

### Input/Output logic — conditional norms and composition
Makinson and van der Torre's I/O logic models norms as input-output pairs (input condition → output obligation/permission) and gives composition rules for chaining them. v4 uses this to model **pathways** — for example, "dividend-to-unrestricted-sub" composes a designate_unrestricted_subsidiary power with an investment pathway with a dividend permission scoped over the unsub's equity. The composition is a graph traversal, not a prompt-level legal argument.

---

## 3. Primitive layer

The deontic graph has exactly eight kinds of thing. Every v4 entity maps to one of these. Anything that does not fit is either a provenance attribute on one of these or out of scope.

### 3.1 party
The subject or holder of a norm. Parties are instantiated per-deal from role identifiers in the agreement (Borrower, each Restricted Subsidiary, each Unrestricted Subsidiary, Holdings, Intermediate Holdings, the Administrative Agent, Required Lenders, etc.). A party's `party_role` (e.g., `borrower`, `loan_party`, `restricted_sub`, `unrestricted_sub`, `holdings`, `agent`, `required_lenders`) is the stable handle; the deal-specific name (e.g., "Duck Creek Technologies, Inc.") is a provenance-level detail. Abstract parent `party`; concrete subtypes as the roles above.

### 3.2 action_class
The kind of action a norm regulates. Closed taxonomy. For RP the eight-to-nine action classes are:

- `make_dividend_payment` — cash or in-kind distribution on equity interests
- `repurchase_equity` — buyback or retirement of equity
- `pay_subordinated_debt` — principal or interest on junior debt (Restricted Debt Payment)
- `make_investment` — investment subject to the Investments covenant (overlaps RP at reallocation)
- `designate_unrestricted_subsidiary` — the power whose exercise changes perimeter
- `make_intercompany_payment` — dividends/payments between loan parties or affiliates
- `make_tax_distribution` — pass-through tax distributions to equity holders
- `pay_holdco_overhead` — management-fee-type payments to sponsor or holding company
- `transfer_material_intellectual_property` — asset-transfer action governed by the J.Crew blocker

Action classes may subsume one another: `make_dividend_payment` is the concrete action, while `make_restricted_payment` is its abstract parent. Enumeration lives in TypeDB, not code.

### 3.3 instrument_class (a sub-hierarchy of object_class)
The financial instrument a norm scopes. RP-relevant instruments are `equity_interest`, `subordinated_debt_instrument`, `holdco_equity`, `unrestricted_sub_equity`, `material_intellectual_property`, and `restricted_sub_equity`. Instruments have structured attributes (seniority, collateralization, whether they are inside or outside the credit perimeter) used by condition-evaluation functions.

**`instrument_class` is an abstract sub-hierarchy under the broader `object_class` hierarchy** (schema in §4.4). Non-instrument objects a norm can scope (e.g., `cash`, `business_division`, `unrestricted_subsidiary_equity_or_assets`) sit alongside `instrument_class` beneath `object_class`. The relation `norm_scopes_object` accepts any member of the `object_class` hierarchy; `norm_scopes_instrument` is the narrower refinement that populates only when the object is specifically a financial instrument.

### 3.4 state_predicate
A boolean proposition about the world at a point in time — for example, `no_event_of_default_exists`, `first_lien_net_leverage_ratio_at_or_below(ratio)`, `pro_forma_compliance_with_financial_covenants`, `proposed_action_is_no_worse_pro_forma(metric)`, `incurrence_test_satisfied(ratio_type, threshold)`, `retained_asset_sale_proceeds(amount)` (the fact that asset-sale proceeds survive the Asset Sale Sweep and are available as a capacity source). State predicates are the leaves of condition trees (§3.5); they are also the targets of `norm_contributes_to_capacity` when a norm's capacity derives from a state rather than from another norm. Their evaluation is delegated to TypeDB functions (Rule 5.2).

### 3.5 condition
An entity representing a single node in a composable predicate tree. `condition.operator` ∈ {`atomic`, `and`, `or`, `not`, `for_all`, `exists`}. An `atomic` condition references a `state_predicate` via `condition_references_predicate`; compound conditions have children via `condition_has_child` with an ordered `child_index`. A condition tree with no atomic leaves is invalid (validator catches it). "No EoD AND (ratio ≤ 5.75 OR no-worse)" is stored as a three-node tree, never flattened (Rule 2.4).

### 3.6 norm
The first-class unit of the deontic layer. A norm has:

- `norm_id` (@key)
- `modality` ∈ {`permission`, `prohibition`, `obligation`, `power`, `immunity`, `exception`} — Hohfeldian modal positions. `permission` and `prohibition` are deontic; `obligation` pairs with a violation consequent (Anderson); `power` is the Hohfeldian capability to alter legal positions (e.g., the Required Lenders' power to amend, the Borrower's power to designate an Unrestricted Subsidiary); `immunity` is the correlative of disability (a party's protected status, e.g., Required Lenders' immunity from Borrower's amendment power over sacred rights); `exception` is a defeater-bearing norm that withdraws scope from another norm
- `capacity_composition` ∈ {`additive`, `fungible`, `shared_pool`, `categorical`, `computed_from_sources`, `unlimited_on_condition`, `n_a`} — how the norm's capacity composes with other norms' capacities: `additive` (separate caps that sum), `fungible` (capacity usable across action classes via reallocation), `shared_pool` (multiple norms draw from a single numerical pool via `shares_capacity_pool`), `categorical` (per-category caps that do not sum), `computed_from_sources` (builder-style capacity = sum of typed source entities), `unlimited_on_condition` (ratio-basket style — uncapped when the condition holds), `n_a` (no capacity applies, e.g., prohibitions)
- `action_scope` ∈ {`specific`, `general`, `reallocable`}
- `cap_usd` (optional, for capped norms)
- `cap_grower_pct` (optional, EBITDA percentage)
- `cap_uses_greater_of` (boolean, "greater of $X or Y% EBITDA")
- `provenance`: `source_text`, `source_section`, `source_page`, `confidence`
- Relations to one party (subject via `norm_binds_subject`), zero-or-more parties (beneficiary via `norm_held_by`), one action_class (`norm_scopes_action`), zero-or-more instrument_classes (`norm_scopes_instrument`), zero-or-more object anchors (`norm_scopes_object`), one condition tree (`norm_has_condition`), zero-or-one violation consequent (`norm_has_violation_consequent`), one-or-more extracted facts (`norm_extracted_from`, Rule 3.2).

### 3.7 defeater
An entity expressing a defeat mechanism: "this defeater defeats that norm under condition C." A defeater has `defeater_type` ∈ {`exception`, `lex_specialis`, `lex_posterior`, `higher_consent`} and a `condition` (the predicate under which the defeat applies). The four defeater types, drawn from the defeasible-deontic-logic and canon-of-construction literature:

- `exception` — an explicit carveout in the agreement (e.g., the Ratio RP basket 6.06(o) defeats the general RP prohibition when the ratio condition holds)
- `lex_specialis` — a more specific norm defeats a more general norm covering the same action (e.g., a specific intercompany payment permission defeats the general prohibition against payments outside the credit group)
- `lex_posterior` — a later-in-time norm (typically from an amendment) defeats an earlier norm on the same subject
- `higher_consent` — consent from a higher-authority lender class (Required Lenders, All Lenders, affected lenders) releases a norm that would otherwise apply

Defeaters connect to norms via the typed `defeats` relation (Rule 2.5), never via entity subclassing.

### 3.8 event
A concrete or hypothetical happening that a norm is evaluated against. `event_instance` has an `event_kind` (e.g., `proposed_dividend`, `proposed_investment`, `proposed_sale_of_division`), structured attributes (`amount_usd`, `target_party`, `ratio_snapshot_first_lien_net_leverage`, `is_no_worse_pro_forma`), and `is_hypothetical` boolean. Question 6 ("if the ratio is 6.0x…") is modeled by constructing an event_instance with `ratio_snapshot_first_lien_net_leverage = 6.0`, `is_no_worse_pro_forma = true`, and passing it to the condition evaluator.

**Pilot-scope design note.** In v4 pilot scope, `event_instance` carries both:

- **World-state facts** (ratio snapshots, EoD status, pro forma compliance flags) that condition evaluation reads
- **Proposed-action details** (action_class, amount, target) that norm applicability filters against

Some theoretical deontic-logic treatments separate these into distinct `world_state` and `event` entities. The pilot conflates them because Duck Creek's six gold-standard questions always evaluate a single proposed action against a single world state, making the separation a distinction without operational difference. If future work needs to evaluate multiple hypothetical actions against the same world state, the separation can be introduced via a schema migration that moves world-state attributes from `event_instance` to a new `world_state` entity referenced via a new `event_takes_place_in` relation.

---

## 4. Schema design

The full TypeQL for the deontic layer lives in `app/data/schema_v4_deontic.tql`. Below is the authoritative specification. TypeDB 3.x syntax throughout: explicit `entity`/`relation`/`attribute` kinds; subtyping via `sub`; relation roles via `relates`; attribute ownership via `owns`; no `rule`, only `fun` (Rule 6.1).

**TypeDB 3.x syntax note.** Where the TypeQL examples in this section declare an abstract subtype, the `@abstract` annotation appears in a separate statement from `sub` per TypeDB 3.x rules (the two cannot combine — error `ANN9`). Examples below show the actual loadable syntax. Inline annotations on top-level abstract types (`party @abstract`, `action_class @abstract`, `object_class @abstract`) are permitted and used where applicable.

### 4.1 Attribute definitions

```tql
define

# identifiers
attribute norm_id, value string;
attribute party_id, value string;
attribute condition_id, value string;
attribute event_instance_id, value string;

# modality + classification closed taxonomies (values enforced at projection)
attribute modality, value string;              # permission|prohibition|obligation|power|immunity|exception
attribute capacity_composition, value string;  # additive|fungible|shared_pool|categorical|computed_from_sources|unlimited_on_condition|n_a
attribute action_scope, value string;          # specific|general|reallocable
attribute condition_operator, value string;    # atomic|and|or|not|for_all|exists
attribute defeater_type, value string;         # exception|lex_specialis|lex_posterior|higher_consent
attribute party_role, value string;            # borrower|loan_party|restricted_sub|unrestricted_sub|holdings|agent|required_lenders

# action + object class labels (closed taxonomies in TypeDB)
attribute action_class_label, value string;
attribute object_class_label, value string;
attribute instrument_class_label, value string;
attribute state_predicate_label, value string;

# norm scalars
attribute cap_usd, value double;
attribute cap_grower_pct, value double;
attribute cap_uses_greater_of, value boolean;
attribute computed_from_sources, value boolean;  # true when norm's capacity is a sum of typed source entities

# event snapshots
attribute ratio_snapshot_first_lien_net_leverage, value double;
attribute ratio_snapshot_senior_secured_leverage, value double;
attribute ratio_snapshot_total_leverage, value double;
attribute is_no_worse_pro_forma, value boolean;
attribute is_hypothetical, value boolean;
attribute proposed_amount_usd, value double;

# condition metadata
attribute child_index, value integer;

# capacity contribution metadata (attribute on norm_contributes_to_capacity relation, not on norms)
attribute aggregation_function, value string;  # greatest_of|sum|min|max — how multiple norm_contributes_to_capacity edges compose

# state_predicate instance metadata — thresholds and comparison mode live on the
# predicate instance, not in the label. A label like "first_lien_net_leverage_at_or_below"
# with threshold_value_double 5.75 is a distinct instance from the same label with
# threshold 6.25; predicate_holds reads the threshold via `$pred has threshold_value_double`.
# reference_predicate_label points to another predicate (used by pro_forma_no_worse
# to name the baseline predicate the counterfactual is "no worse than").
attribute threshold_value_double, value double;
attribute threshold_value_string, value string;
attribute operator_comparison, value string;      # at_or_below|at_or_above|equals|less_than|greater_than
attribute reference_predicate_label, value string;

# provenance (reused from v3 schema; listed here for completeness of the deontic layer)
# attribute source_text, value string;
# attribute source_section, value string;
# attribute source_page, value integer;
# attribute confidence, value double;
```

### 4.2 Party hierarchy

```tql
entity party @abstract,
    owns party_id @key,
    owns party_role;

entity borrower_party sub party;
entity loan_party sub party;             # guarantor pool
entity restricted_sub_party sub party;
entity unrestricted_sub_party sub party;
entity holdings_party sub party;
entity agent_party sub party;
entity required_lenders_party sub party;
```

### 4.3 Action class hierarchy

```tql
entity action_class @abstract,
    owns action_class_label @key;

# TypeDB 3.x: @abstract cannot combine with sub in one statement.
# Declared in two statements.
entity make_restricted_payment sub action_class;
entity make_restricted_payment @abstract;
entity make_dividend_payment sub make_restricted_payment;
entity repurchase_equity sub make_restricted_payment;
entity pay_subordinated_debt sub action_class;
entity make_investment sub action_class;
entity designate_unrestricted_subsidiary sub action_class;
entity make_intercompany_payment sub action_class;
entity make_tax_distribution sub make_restricted_payment;
entity pay_holdco_overhead sub make_restricted_payment;
entity transfer_material_intellectual_property sub action_class;
```

### 4.4 Object class hierarchy (RP-relevant subset)

A norm scopes an **object** — the thing the action applies to. The `object_class` hierarchy is a closed taxonomy seeded in schema and is the canonical SSoT for object-class labels (Rule 1.1). Financial instruments form an abstract sub-hierarchy (`instrument_class`) beneath `object_class`; non-instrument objects (`cash`, `business_division`, `unrestricted_subsidiary_equity_or_assets`) sit alongside.

```tql
entity object_class @abstract,
    owns object_class_label @key;

# financial instruments — a sub-abstract under object_class.
# TypeDB 3.x: @abstract cannot combine with sub in one statement.
# Declared in two statements.
entity instrument_class sub object_class,
    owns instrument_class_label;
entity instrument_class @abstract;

entity equity_interest sub instrument_class;
entity holdco_equity sub equity_interest;
entity restricted_sub_equity sub equity_interest;
entity unrestricted_sub_equity sub equity_interest;
entity subordinated_debt_instrument sub instrument_class;
entity material_intellectual_property sub instrument_class;

# non-instrument objects
entity cash sub object_class;
entity unrestricted_subsidiary_equity_or_assets sub object_class;
entity business_division sub object_class;
```

The six object-class labels listed in the requirements (`cash`, `equity_interest`, `subordinated_debt_instrument`, `unrestricted_subsidiary_equity_or_assets`, `business_division`, `material_intellectual_property`) all appear above as concrete entity subtypes. Additional Duck-Creek-visible refinements (`holdco_equity`, `restricted_sub_equity`, `unrestricted_sub_equity`) hang beneath `equity_interest` so narrower-scope norms can anchor precisely.

### 4.5 State predicate

```tql
entity state_predicate,
    owns state_predicate_label @key,
    owns threshold_value_double,
    owns threshold_value_string,
    owns operator_comparison,
    owns reference_predicate_label,
    owns source_text,
    owns source_section,
    owns source_page;
```

Concrete predicate labels (enumerated in seed, not entity subtypes — each predicate's evaluation lives in a function keyed on its label): `no_event_of_default_exists`, `first_lien_net_leverage_at_or_below`, `senior_secured_leverage_at_or_below`, `total_leverage_at_or_below`, `pro_forma_no_worse`, `incurrence_test_satisfied`, `pro_forma_compliance_financial_covenants`, `board_approval_obtained`, `officer_certificate_delivered`, `qualified_ipo_has_occurred`, `retained_asset_sale_proceeds` (Judgment 3: asset-sale proceeds that survive the Asset Sale Sweep; appears as a capacity source to the builder basket via `norm_contributes_to_capacity`, not as an action class).

**Threshold values are per-instance attributes, not baked into labels.** A norm whose condition is "first lien net leverage ≤ 5.75" and another whose condition is "first lien net leverage ≤ 6.25" reference the *same* `state_predicate_label` (`first_lien_net_leverage_at_or_below`) with different `threshold_value_double` values on their respective `state_predicate` instances. The projection layer (Prompt 07) creates one `state_predicate` instance per distinct (label, threshold) pair extracted from the agreement, and each condition's atomic leaf references the appropriate instance. `operator_comparison` defaults to the semantic implied by the label (`at_or_below`), but the attribute lets projection override for cleaner generalisation if needed. `reference_predicate_label` is populated for predicates like `pro_forma_no_worse` whose semantics require pointing to the baseline predicate they compare against.

#### 4.5.1 Predicate label contract

State predicate instances use **clean labels** — the predicate concept name without threshold values embedded in the string. Thresholds are stored as instance attributes (`threshold_value_double`, `threshold_value_string`, `operator_comparison`, `reference_predicate_label`), not encoded in the label suffix.

**Correct:** `state_predicate_label="first_lien_net_leverage_at_or_below"`, `threshold_value_double=5.75`, `operator_comparison="at_or_below"`

**Incorrect (deprecated):** `state_predicate_label="first_lien_net_leverage_at_or_below_5_75"` (threshold in label suffix)

This contract is binding for all layers:

- **Extraction** (v3 pipeline with Prompt 07 additions) — produces clean labels.
- **Projection** (Prompt 07's projection engine) — emits state_predicate instances with clean labels plus threshold attributes.
- **Ground truth** (Prompt 05's `duck_creek_rp_ground_truth.yaml`) — references predicates by clean label, threshold by attribute.
- **Functions** (`predicate_holds` and downstream) — read threshold from instance attribute, not from label parsing.

The round-trip check at Prompt 08 compares extracted predicate labels to ground-truth labels. Label format drift between layers will cause spurious round-trip failures. Flag any code path producing suffixed labels as a bug.

### 4.6 Condition entity and recursive tree relation

```tql
entity condition,
    owns condition_id @key,
    owns condition_operator;

relation condition_references_predicate,
    relates condition,
    relates predicate;
condition plays condition_references_predicate:condition;
state_predicate plays condition_references_predicate:predicate;

relation condition_has_child,
    relates parent,
    relates child,
    owns child_index;
condition plays condition_has_child:parent;
condition plays condition_has_child:child;
```

**Operator support in TypeDB 3.x.** `condition_holds` supports `atomic`, `or`, and `and` natively. `and` uses a count-based implementation (total child count vs count of children whose atomic predicate holds) verified against two fixtures during a pre-Prompt-05 probe — it avoids recursion through negation, which TypeDB 3.x (error `FUN9`) refuses. `and` is currently restricted to depth-2 (AND of atomic children); deeper nested AND-of-OR-of-atomics is handled by flattening at projection time into either (a) a disjunction of conjunctions or (b) a conjunction of disjunctions as appropriate. `not`, `for_all`, and `exists` are not supported in the pilot. `not` is handled architecturally by defining state predicates in positive form (e.g., `no_event_of_default_exists` rather than `NOT event_of_default_exists`), which matches the predicate list in §4.5. `for_all` and `exists` are not needed for RP pilot scope.

### 4.7 Norm entity and its relations

```tql
entity norm,
    owns norm_id @key,
    owns modality,
    owns capacity_composition,
    owns action_scope,
    owns cap_usd,
    owns cap_grower_pct,
    owns cap_uses_greater_of,
    owns computed_from_sources,
    owns source_text,
    owns source_section,
    owns source_page,
    owns confidence;

relation norm_binds_subject,
    relates norm,
    relates subject;
norm plays norm_binds_subject:norm;
party plays norm_binds_subject:subject;

relation norm_held_by,
    relates norm,
    relates beneficiary;
norm plays norm_held_by:norm;
party plays norm_held_by:beneficiary;

relation norm_scopes_action,
    relates norm,
    relates action;
norm plays norm_scopes_action:norm;
action_class plays norm_scopes_action:action;

relation norm_scopes_instrument,
    relates norm,
    relates instrument;
norm plays norm_scopes_instrument:norm;
instrument_class plays norm_scopes_instrument:instrument;

# object is any member of the object_class hierarchy (§4.4):
# instrument_class subtypes, cash, business_division, or unrestricted_subsidiary_equity_or_assets
relation norm_scopes_object,
    relates norm,
    relates object;
norm plays norm_scopes_object:norm;
object_class plays norm_scopes_object:object;

relation norm_has_condition,
    relates norm,
    relates root;
norm plays norm_has_condition:norm;
condition plays norm_has_condition:root;

# violation consequent — Anderson reduction
entity violation_consequent,
    owns source_text,
    owns source_section,
    owns source_page;

relation norm_has_violation_consequent,
    relates norm,
    relates consequent;
norm plays norm_has_violation_consequent:norm;
violation_consequent plays norm_has_violation_consequent:consequent;

# provenance anchor — norm to the extracted fact(s) that justify it (Rule 3.2)
relation norm_extracted_from,
    relates norm,
    relates fact;
norm plays norm_extracted_from:norm;
# Role aliases for extractable v3 entities (builder_basket, ratio_basket, jcrew_blocker, etc.)
# are declared as `plays` statements in §4.10. Adding a new extractable entity type requires
# adding one `plays norm_extracted_from:fact` declaration in `schema_v4_deontic.tql` and
# one `deontic_mapping` row (Prompt 07).

# capacity contribution — for additive/computed_from_sources composition
relation norm_contributes_to_capacity,
    relates contributor,
    relates pool,
    owns child_index,
    owns aggregation_function;
norm plays norm_contributes_to_capacity:contributor;
norm plays norm_contributes_to_capacity:pool;
```

The `aggregation_function` attribute on the relation (not on either norm) records how contributing-norm capacities compose into the pool norm. Builder basket uses `greatest_of` across its source norms (CNI source vs ECF source vs EBITDA fixed-charge source vs starter). Additive capacity uses `sum`. Other aggregation modes are reserved for future covenants.

### 4.8 Defeater and the defeats edge

```tql
entity defeater,
    owns defeater_type,
    owns source_text,
    owns source_section,
    owns source_page;

relation defeater_has_condition,
    relates defeater,
    relates root;
defeater plays defeater_has_condition:defeater;
condition plays defeater_has_condition:root;

relation defeats,
    relates defeater,
    relates defeated;
defeater plays defeats:defeater;
norm plays defeats:defeated;
```

### 4.9 Event instance entity and evaluation relations

```tql
# event_instance carries both world-state snapshots (ratio values, EoD flags)
# and proposed-action details (action_class, amount, target) in pilot scope.
# See §3.8 for the rationale and the migration path if separation is needed later.
entity event_instance,
    owns event_instance_id @key,
    owns action_class_label,           # which action is proposed
    owns proposed_amount_usd,
    owns is_hypothetical,
    owns ratio_snapshot_first_lien_net_leverage,
    owns ratio_snapshot_senior_secured_leverage,
    owns ratio_snapshot_total_leverage,
    owns is_no_worse_pro_forma;

relation event_targets_party,
    relates event,
    relates target;
event_instance plays event_targets_party:event;
party plays event_targets_party:target;

relation event_targets_instrument,
    relates event,
    relates instrument;
event_instance plays event_targets_instrument:event;
instrument_class plays event_targets_instrument:instrument;
```

### 4.10 Role-aliasing extracted entities as `fact`

The projection layer extends the existing `provision_has_extracted_entity` family by declaring a role alias so any extracted entity (builder_basket, ratio_basket, jcrew_blocker, investment_pathway, basket_reallocates_to, etc.) can play the `fact` role in `norm_extracted_from`. This is done in `schema_v4_deontic.tql` via `define`:

```tql
# Example alias (builder_basket plays `fact`):
builder_basket plays norm_extracted_from:fact;
ratio_basket plays norm_extracted_from:fact;
general_rp_basket plays norm_extracted_from:fact;
jcrew_blocker plays norm_extracted_from:fact;
investment_pathway plays norm_extracted_from:fact;
basket_reallocates_to plays norm_extracted_from:fact;
# etc., one per v3 extracted entity that projects to a norm
```

**Extensibility note.** This list grows when new covenants are added to v4. Each new extractable entity type in v3 (e.g., MFN entities, DI entities when those covenants move to deontic modeling) must add a `plays norm_extracted_from:fact` declaration here, and a corresponding `deontic_mapping` row in the projection seed. The list is maintained by hand; no auto-generation — explicitness is the point (Rule 1.1, SSoT).

### 4.11 What is intentionally NOT in the deontic schema

- No `synthesis_guidance` attribute. The category-level guidance strings on `ontology_category` remain for v3 compatibility but are ignored by v4 (Rule 2.1).
- No flattened boolean attributes of the form `requires_no_eod` or `requires_ratio_below_X` on norms (Rule 2.4). All such content is in the `condition` tree.
- No hardcoded norm_id generator logic. norm_id is `{provision_id}:{modality}:{action_class_label}:{index}` deterministically computed in the projection layer.

---

## 5. Function library

All deontic logic lives in TypeDB functions (Rule 5.2), organized into files matching the existing per-concern pattern. Python callers are thin.

**Signature convention.** Functions take entity concepts directly (e.g., `state_predicate`, `condition`, `event_instance`, `norm`) rather than their string identifiers. This sidesteps TypeDB 3.x's value/attribute variable-binding restriction: a function parameter declared `string` cannot be bound from a variable pattern-matched as `has state_predicate_label $x` — the driver treats the same variable as attribute in one context and value in another and refuses the function call. Callers pass entity concepts obtained via `match` queries. Python operation wrappers (Prompt 10) accept string ids at the API boundary and perform the lookup before invoking functions. Where a label or threshold is *not* pattern-bound via `has` (e.g., `$action_label: string` passed in literally by the caller), string parameters remain.

**World-state representation.** The "world state" argument is a concrete `event_instance` carrying the ratio snapshots and flags that predicates read. Functions take `$ws: event_instance` directly. There is no separate `world_state` entity — `event_instance` serves both as "the action being proposed" and "the state snapshot against which predicates evaluate."

### 5.1 `app/data/deontic_condition_functions.tql`

```
fun predicate_holds($pred: state_predicate, $ws: event_instance) -> boolean
```
Evaluates a single state predicate against an event_instance. Reads `threshold_value_double`, `operator_comparison`, and `reference_predicate_label` from the predicate instance where relevant. Atomic leaf of condition evaluation.

```
fun child_count($cond: condition) -> integer
fun holding_atomic_child_count($cond: condition, $ws: event_instance) -> integer
```
Helpers used by `condition_holds` for the AND branch (count total children vs count of children whose atomic predicate holds).

```
fun condition_holds($cond: condition, $ws: event_instance) -> boolean
```
Evaluates a condition tree. Supports `atomic`, `or`, and `and` (depth-2 per §4.6). `not`/`for_all`/`exists` are not supported in the pilot.

### 5.2 `app/data/deontic_norm_functions.tql`

```
fun applicable_permissions($action_label: string, $subject_role: string, $object_label: string, $ws: event_instance) -> { norm }
fun applicable_prohibitions($action_label: string, $subject_role: string, $object_label: string, $ws: event_instance) -> { norm }
```
Streams of norms matching action / subject_role / object_label filters and in force in `$ws`. Object-scope semantics: a norm either has a matching `norm_scopes_object` edge or has no object-scope edge at all.

```
fun norm_is_defeated($n: norm, $ws: event_instance) -> boolean
```
True iff some defeater connected to `$n` via `defeats` has a `defeater_has_condition` whose condition holds in `$ws`.

```
fun norm_is_in_force($n: norm, $ws: event_instance) -> boolean
```
Composition: not defeated AND (no attached condition OR attached condition holds). Wraps `norm_is_defeated` + `condition_holds`.

```
fun norm_is_structurally_complete($n: norm) -> boolean
```
True iff the norm has: modality, source_text/section/page, subject (via `norm_binds_subject`), and at least one of `norm_scopes_action` or `norm_scopes_instrument`. Storage rejects norms where this returns false (Rule 2.3).

### 5.3 `app/data/deontic_capacity_functions.tql`

```
fun additive_capacity($action_label: string, $subject_role: string, $object_label: string, $ws: event_instance) -> double
```
Sum of `cap_usd` across applicable permissions with `capacity_composition == "additive"`.

```
fun categorical_capacities($action_label: string, $subject_role: string, $ws: event_instance) -> { norm, cap_usd }
```
Stream of (norm, cap_usd) for applicable permissions with `capacity_composition == "categorical"`. Return's second slot is the `cap_usd` attribute concept (not a bare `double`) — TypeDB 3.x stream returns must match inferred attribute type of the source variable.

```
fun has_unlimited_conditional_capacity($action_label: string, $subject_role: string, $object_label: string, $ws: event_instance) -> boolean
```
True iff any applicable permission has `capacity_composition == "unlimited_on_condition"`.

```
fun computed_from_sources_capacity_greatest_of($norm_id: string, $ws: event_instance) -> double
fun computed_from_sources_capacity_sum($norm_id: string, $ws: event_instance) -> double
```
For a pool norm whose `capacity_composition` is `computed_from_sources`, applies the named aggregation across contributing norms via the `aggregation_function` attribute on `norm_contributes_to_capacity`. Split into per-aggregation variants because TypeDB 3.x functions allow only one `reduce` per body. Python operations (Prompt 10) dispatch by reading the pool norm's `aggregation_function` before calling.

```
fun reallocated_capacity_to($target_action_label: string, $subject_role: string, $ws: event_instance) -> double
```
PILOT STUB: returns 0 until Prompt 07 projects v3's `basket_reallocates_to` edges into `norm_contributes_to_capacity` bridges. Parameter-use guards reference all three arguments.

### 5.4 `app/data/deontic_pathway_functions.tql`

```
fun norm_enables_hop($action_label: string, $from_state: string, $to_state: string, $ws: event_instance) -> boolean
```
PILOT: true iff some applicable permission exists with an object whose `object_class_label` matches `$to_state`. Prompt 06 will add typed state-transition relations for richer modelling.

```
fun state_reachable($from_state: string, $to_state: string, $ws: event_instance, $max_hops: integer) -> boolean
```
Bounded-depth reachability. Base case: `$from_state == $to_state`. One-hop: some in-force permission targeting `$to_state`. Deeper recursion deferred.

### 5.5 `app/data/deontic_validation_functions.tql`

```
fun norm_has_required_fields($n: norm) -> boolean
fun norm_has_scope($n: norm) -> boolean
```
Splitter functions used by the storage gate: `norm_has_required_fields` checks modality + all three source attributes + at least one `norm_binds_subject`; `norm_has_scope` checks at least one `norm_scopes_action` or `norm_scopes_instrument`. `norm_is_structurally_complete` (§5.2) is their conjunction.

```
fun covenant_missing_expected_norm_kinds($covenant: string, $deal_id: string) -> { modality }
```
PILOT STUB returning empty stream until the `expected_norm_kind` seed lands (Prompt 06). Signature is stable; body will diff against the seed's per-covenant expected set. Return-type stream slot is `modality` (attribute concept) rather than bare `string` to match TypeDB 3.x stream return inference.

### 5.6 `app/data/deontic_pattern_functions.tql`

```
fun has_unlimited_conditional_without_cap($deal_id: string) -> boolean
```
Detects the trapdoor pattern: a permission whose capacity is `unlimited_on_condition` with no fallback `cap_usd`.

```
fun has_exception_defeating_critical_prohibition($deal_id: string) -> boolean
```
Detects when a critical prohibition (make_dividend_payment or transfer_material_intellectual_property) has an active `defeats` edge.

```
fun has_undefined_reference_term($deal_id: string) -> boolean
```
PILOT STUB returning false until Prompt 07's cross-reference between norm `source_text` and v3's ip/transfer/materiality definitions lands.

Additional detectors (`has_jcrew_pattern_deontic`, `has_serta_pattern_deontic`, `has_collateral_leakage_pattern_deontic`) will be added as their constitutive norm shapes are characterised during projection (Prompt 07).

---

## 6. Operation schema

v4 answers questions by composing a finite set of operations (Rule 5.4). Each operation has a typed parameter schema and a typed return shape. The intent parser (`app/services/intent_parser.py`) converts a natural-language question into an `IntentObject` that selects an operation and fills its parameters. The operation (`app/services/deontic_operations.py`) calls TypeDB functions and returns a structured `OperationResult`. The renderer (`app/services/deontic_renderer.py`) converts the result to prose. No operation contains legal reasoning; no renderer contains legal reasoning (Rules 5.2, 5.3, 4.4).

### 6.1 The 11 operations (names and one-line purposes)

1. `describe_norm` — return prose descriptions of the norm(s) matching a filter
2. `get_attribute` — return a specific attribute value of a referenced entity (e.g., starter dollar amount)
3. `enumerate_linked` — list all entities of a given type attached to a provision
4. `evaluate_capacity` — compute capacity for an action, composing basket types
5. `evaluate_feasibility` — answer "can X happen under Y snapshot" by applicability + defeater check
6. `enumerate_defeaters` — list exceptions attached to a given norm
7. `trace_pathways` — enumerate multi-hop compositions between anchors (action classes *or* state predicates)
8. `describe_relation` — describe the semantics of a relation instance (e.g., a specific reallocation edge)
9. `lookup_definition` — fetch a defined-term record (e.g., IP definition, Materiality definition)
10. `filter_norms` — generic filter over (modality × action × scope × composition) tuples
11. `enumerate_patterns` — return detected patterns (J.Crew, Serta, etc.) with their constituent norms

### 6.2 Parameter details for the 5 operations that serve Duck Creek

Parameters are typed and JSON-serializable. Closed-enum parameters reference TypeDB-seeded taxonomies (Rule 4.2).

**`describe_norm`**
```
{
  "provision_id": string,
  "filter": {
    "modality": "permission" | "prohibition" | "obligation" | "power" | "immunity" | "exception" | null,
    "action_class": string | null,
    "object_class": string | null,          # e.g., "unrestricted_sub_equity"
    "scope": "specific" | "general" | "reallocable" | null
  },
  "include_source_text": boolean            # default true
}
```
Returns `{ "norms": [ { norm_id, modality, action, scope, cap_usd, cap_grower_pct, condition_tree, source_section, source_text, provenance_anchors: [entity_ids] } ] }`.

**`evaluate_feasibility`**
```
{
  "provision_id": string,
  "action_class": string,
  "object_class": string | null,
  "hypothetical_impact": {
    "proposed_amount_usd": double | null,
    "target_party_role": string | null,
    "ratio_snapshot_first_lien_net_leverage": double | null,
    "ratio_snapshot_senior_secured_leverage": double | null,
    "ratio_snapshot_total_leverage": double | null,
    "is_no_worse_pro_forma": boolean | null
  }
}
```
Returns `{ "verdict": "permitted" | "prohibited" | "conditional", "permissions_fired": [...], "prohibitions_fired": [...], "defeaters_applied": [...], "limiting_condition": condition_tree | null, "citations": [source_section...] }`.

**`enumerate_linked`**
```
{
  "provision_id": string,
  "entity_type": string,                    # e.g., "builder_basket", "jcrew_blocker"
  "include_children": boolean,              # default true (e.g., include basket sources)
  "include_annotations": boolean            # default true
}
```
Returns `{ "entities": [ { entity_type, attributes: {...}, children: [...], annotations: [...] } ] }`.

**`trace_pathways`**
```
{
  "provision_id": string,
  "source": { "kind": "action_class" | "state_predicate", "label": string },
  "target": { "kind": "action_class" | "state_predicate", "label": string },
  "direction": "forward" | "backward",
  "max_hops": integer,                      # default 3
  "quantification_mode": "all" | "first" | "shortest"
}
```
Source and target anchors are polymorphic (Judgment 3): a capacity-sourcing question like Q4 traces from a state_predicate (`retained_asset_sale_proceeds`) through the builder basket's `norm_contributes_to_capacity` chain to an action_class target; an action-composition question like Q3 traces action_class → action_class. A hop may be either a norm (which enables the next action) or a `norm_contributes_to_capacity` edge (which supplies capacity to the next norm).

Returns `{ "pathways": [ { hops: [ { hop_kind: "norm" | "capacity_contribution", norm_id, action_class, object_class, state_predicate_label, cap_usd, source_section } ], total_cap_usd } ] }`.

**`evaluate_capacity`**
```
{
  "provision_id": string,
  "action_class": string,
  "include_reallocated_capacity": boolean,   # Q5 uses true
  "quantification_mode": "additive" | "fungible" | "categorical" | "total",
  "normalize_to": "usd" | "ebitda_pct"       # presentation hint; value returned in both
}
```
Returns `{ "total_usd": double, "total_ebitda_pct": double, "components": [ { norm_id, action_class, cap_usd, cap_grower_pct, capacity_composition, source_section } ], "reallocation_inflows": [ { from_norm, to_norm, cap_usd } ] }`.

### 6.3 The other six operations (parameter schemas, briefly)

- `get_attribute { provision_id, entity_ref: { type, id }, attribute_name }` → `{ value, source_section, source_text }`
- `enumerate_defeaters { provision_id, norm_id }` → `{ defeaters: [{ defeater_type, condition_tree, source_section, source_text }] }`
- `describe_relation { provision_id, relation_type, relation_ref: { role_a_id, role_b_id } }` → `{ semantics: {...}, source_section }`
- `lookup_definition { provision_id, term: "material_intellectual_property" | "transfer" | "materiality" | ... }` → `{ definition_record: {...} }`
- `filter_norms { provision_id, predicate: expression_tree }` → `{ norms: [...] }`
- `enumerate_patterns { provision_id, pattern_name?: string }` → `{ patterns: [ { name, present: boolean, constitutive_norm_ids: [...], defeaters: [...] } ] }`

---

## 7. The 6 Duck Creek gold-standard questions as acceptance test

The acceptance test is `app/data/gold_standard/lawyer_dc_rp.json` (6 questions). Verbatim questions and expected intents below. An answer "substantively matches" a gold answer iff the key numerical values, section citations, and legal conclusion coincide (exact prose is not required).

### Q1 — Builder basket composition
> **Question:** What test is the build-up basket or available amount basket based on and when does the basket start growing?
> **Gold:** The Cumulative Amount is based on the greatest of three tests: (1) 50% of cumulative Consolidated Net Income (which amount shall not be less than zero for any fiscal quarter), (2) Excess Cash Flow not required to be applied to prepay Term Loans or any other debt (such amount cannot be less than $0 for any fiscal year), (3) cumulative Consolidated EBITDA minus 140% of cumulative Consolidated Fixed Charges. All tests start growing from the first day of the fiscal quarter in which the Closing Date occurs.

**Expected intent:**
```
{ "operation": "describe_norm",
  "parameters": {
    "provision_id": "<dc_rp>",
    "filter": { "modality": "permission", "action_class": "make_restricted_payment", "scope": "general" },
    "include_source_text": true
  } }
```
With renderer logic that includes the norm's `computed_from_sources_capacity` components via a follow-up `enumerate_linked` over the norm's `norm_contributes_to_capacity` children (CNI, ECF, EBITDA-FC sources, starter date).

### Q2 — Dividend of Unrestricted Subsidiary equity
> **Question:** Is the Borrower permitted to dividend the equity it owns in Unrestricted Subsidiaries?
> **Gold:** Yes, under 6.06(p) the Borrower can dividend shares of Equity Interest or any assets of an Unrestricted Subsidiary.

**Expected intent:**
```
{ "operation": "evaluate_feasibility",
  "parameters": {
    "provision_id": "<dc_rp>",
    "action_class": "make_dividend_payment",
    "object_class": "unrestricted_sub_equity",
    "hypothetical_impact": {}
  } }
```

### Q3 — Reallocation from other covenants
> **Question:** Are there any investment, prepayment of other debt or other baskets that can be reallocated and used to make restricted payments or dividends?
> **Gold:** Yes, under 6.06(j) amount available for Restricted Debt Payment under 6.09(a) and amounts available for Investments under 6.03(y) can be reallocated to the making of Dividends. 6.09(a) includes the greater of $130,000,000 and 100% of Consolidated EBITDA. 6.09(a) also includes other more tailored baskets available for Restricted Debt Payments which may or may not be available for reallocation, including intercompany debt payments and payments in connection with a reorganization or IPO.

**Expected intent:**
```
{ "operation": "trace_pathways",
  "parameters": {
    "provision_id": "<dc_rp>",
    "source": { "kind": "action_class", "label": "make_investment" },
    "target": { "kind": "action_class", "label": "make_dividend_payment" },
    "direction": "forward",
    "max_hops": 2,
    "quantification_mode": "all"
  } }
```
Renderer also calls `trace_pathways` with `source = { "kind": "action_class", "label": "pay_subordinated_debt" }` and merges the results.

### Q4 — Asset-sale proceeds to dividends
> **Question:** Can any asset sale proceeds be used to make dividends?
> **Gold:** Yes, Retained Asset Sale Proceeds build the Cumulative Amount which consists of proceeds from: Net Cash Proceeds from asset sales not subject to prepayment on account of Section 2.10(c)(iv), permitting proceeds from any Asset Sale using the unlimited basket 6.05(z) if such Asset Sale is a sale of a product line and the pro forma First Lien Net Leverage Ratio is 6.25x or less or if such test is no worse pro forma. Also includes asset sale proceeds not swept when First Lien Net Leverage Ratio is 5.75x or less (50% of proceeds) or 5.50x or less (100% of proceeds). Also includes Net Cash Proceeds from non-collateral assets, ordinary course asset sales, asset sales from non-ratio baskets, casualty events, and proceeds from collateral assets below de minimis thresholds of $20M/15% EBITDA individual and $40M/30% EBITDA annual.

**Expected intent:** composite. Per Judgment 3, the asset sale is not an RP action — it is an Asset-Sales-covenant action that *produces* a `retained_asset_sale_proceeds` state; that state then feeds builder-basket capacity, which in turn enables the dividend action. The path has four nodes (asset_sale action → retained proceeds state → builder capacity → dividend action) and three edges. `trace_pathways` is anchored at the state predicate:
```
{ "operation": "trace_pathways",
  "parameters": {
    "provision_id": "<dc_rp>",
    "source": { "kind": "state_predicate", "label": "retained_asset_sale_proceeds" },
    "target": { "kind": "action_class", "label": "make_dividend_payment" },
    "direction": "forward",
    "max_hops": 3,
    "quantification_mode": "all"
  } }
```
Followed by `enumerate_linked` for `sweep_tier`, `asset_sale_sweep`, and the builder's `asset_proceeds_source` children so the renderer can describe the sweep mechanics (de minimis thresholds, ratio-based sweep reductions, non-collateral exemptions) that determine *which* proceeds are retained. The composition logic lives in the renderer's prompt-free orchestration (§5.3 renderer is prose-only; sequencing of typed operation results is orchestration, not reasoning).

### Q5 — Total quantifiable dividend capacity
> **Question:** Determine the total amount of quantifiable dividend capacity.
> **Gold:** $520m (or 409.9% of EBITDA) plus all assets that do not secure the Loans and all non-EBITDA producing assets. RP starter: $130m/100% EBITDA. General RP basket: $130m/100% EBITDA. General prepayment of debt basket: $130m/100% EBITDA. General investment basket: $130m/100% EBITDA.

**Expected intent:**
```
{ "operation": "evaluate_capacity",
  "parameters": {
    "provision_id": "<dc_rp>",
    "action_class": "make_dividend_payment",
    "include_reallocated_capacity": true,
    "quantification_mode": "total",
    "normalize_to": "usd"
  } }
```

### Q6 — Ratio-basket feasibility with hypothetical 6.0x ratio
> **Question:** If the Borrower owns an asset/business division that has assets worth $200m, but EBITDA of such business is negative, can the Borrower dividend the asset/business division to shareholders if the First Lien Net Leverage Ratio is 6.0x?
> **Gold:** Yes, because the Ratio RP basket 6.06(o) permits such transaction as long as the First Lien Net Leverage Ratio, even if above 5.75x, is no worse.

**Expected intent:**
```
{ "operation": "evaluate_feasibility",
  "parameters": {
    "provision_id": "<dc_rp>",
    "action_class": "make_dividend_payment",
    "object_class": null,
    "hypothetical_impact": {
      "proposed_amount_usd": 200000000,
      "ratio_snapshot_first_lien_net_leverage": 6.0,
      "is_no_worse_pro_forma": true
    }
  } }
```

---

## 8. Extraction strategy

### 8.1 Preserve — most v3 extraction questions stay unchanged
The 289 RP questions in `questions.tql` already produce typed entities (builder_basket, ratio_basket, jcrew_blocker, pathways, reallocations, sweep_tiers, definitions, unsub_designation, etc.). Every one of those entities is the *fact* that a v4 norm cites via `norm_extracted_from`. v4 does not re-extract what v3 already captures; the projection layer (§8.3) transforms them into norms.

### 8.2 Four surgical additions
Only four new extraction signals are required to make the v3 facts sufficient for projection. These are added to `questions.tql` with new question entries, not to application code:

1. **`capacity_aggregation_function`** (attribute on every RP basket subtype) ∈ {`additive`, `fungible`, `categorical`, `computed_from_sources`, `unlimited_on_condition`, `n_a`}. Closed taxonomy with per-option definitions in the question text (Rule 4.2). Drives `capacity_composition` on the projected norm.
2. **`object_class`** (attribute on baskets and blockers) when the basket/blocker scopes a specific instrument class (e.g., an unsub_distribution_basket has `object_class = "unrestricted_sub_equity"`). Drives `norm_scopes_object`.
3. **`partial_applicability`** (attribute on `basket_reallocates_to`) — boolean + a textual "partial scope" field capturing "may or may not be available for reallocation" language (Q3's "tailored baskets available for Restricted Debt Payments which may or may not be available for reallocation"). Drives a conditional `defeats` edge on the projected reallocation norm.
4. **`capacity_composition_validation`** (derived question, answered by Claude from the extracted basket text) — a single classification question whose output is compared against the extracted `capacity_aggregation_function` for drift detection (Rule 4.3 classification harness).

### 8.3 Projection layer
The projection layer converts extracted entities to norms declaratively, using a mapping seed that lives in TypeDB. File: `app/data/rp_deontic_mappings.tql`.

Each mapping is a `deontic_mapping_rule` entity with attributes:

- `source_entity_type` (e.g., `builder_basket`, `ratio_basket`, `jcrew_blocker`)
- `target_modality` (e.g., `permission`, `prohibition`)
- `target_action_class_label`
- `target_capacity_composition` (either a constant or a reference to the source entity's `capacity_aggregation_function` attribute)
- `target_scope`
- `cap_source_attribute` (which attribute to pull cap_usd from, e.g., `basket_amount_usd`)
- `grower_source_attribute` (which to pull cap_grower_pct from, e.g., `basket_grower_pct`)
- `condition_template_id` (pointer to a seeded condition-tree template)

The engine (`app/services/deontic_projection.py`) iterates extracted entities for a provision, looks up their mapping, constructs norms, builds condition trees from templates, and writes through `GraphStorage` (which runs `norm_is_structurally_complete` as a pre-insert gate per Rule 2.3). Mappings are data; the engine is ~200 lines of Python with no hardcoded covenant logic.

### 8.4 Why REFACTOR not Reuse or Clean Slate
- **Reuse** (keep v3 and append a norm view over it) was rejected because the v3 storage layer does not enforce the structural invariants (Rule 2.3), and the foundational rules are not retrofittable as an after-the-fact view without accepting partial-norm pollution.
- **Clean Slate** (re-extract everything with new prompts producing norms directly) was rejected because v3 extraction quality on RP is validated and extensive; discarding it risks regressing well-tested extraction to chase an architectural preference. It also violates Rule 7.2 (ground truth is the baseline — changing extraction changes the baseline).
- **Refactor** (keep v3 extraction, add the projection layer + the deontic schema + the four surgical additions) preserves the validated extraction quality, isolates the new layer, and lets the acceptance test fairly compare v3 gold answers against v4 operations over the same extracted facts.

---

## 9. Completeness and classification harnesses

v4 ships with six automated quality harnesses. CI runs them on every commit (Rule 7.3). A drop below threshold blocks merge (Rule 7.4).

### 9.1 Completeness mechanisms

> **Convention.** This section refers to document segments by their canonical `segment_type_id` as defined in `app/data/segment_types_seed.tql`. Where the projection layer consumes segment identity via the RPUniverse dataclass, the mapping to `rp_universe_field` is handled by `app/services/segment_introspector.py`.

**Storage-time structural validation** (`norm_is_structurally_complete` from §5.2). Every norm insert goes through a TypeDB `fun` gate that confirms the five required structural fields (modality, subject, action, condition, source). A false return rejects the insert. This prevents partial-extraction pollution at the earliest possible point (Rule 2.3).

**Per-segment norm-count expectations.** Each RP-relevant document segment has an expected (min, max) norm count seeded in `rp_deontic_mappings.tql`. The seven segment identities below use the canonical `segment_type_id` values from the seed. `segment_expected_norm_count_check` returns false if a provision's segment yields fewer norms than the floor — a signal of under-extraction.

| `segment_type_id` | `rp_universe_field` | Expected norm count |
|---|---|---|
| `definitions` | `definitions` | {min: 0, max: 5} |
| `negative_cov_rp` | `dividend_covenant` | {min: 1, max: 3} |
| `negative_cov_investments` | `investment_covenant` | {min: 0, max: 2} |
| `negative_cov_asset_sales` | `asset_sale_covenant` | {min: 0, max: 2} |
| `negative_cov_rdp` | `rdp_covenant` | {min: 0, max: 1} |
| `unrestricted_sub_mechanics` | `unsub_mechanics` | {min: 0, max: 2} |
| `pro_forma_mechanics` | `pro_forma_mechanics` | {min: 0, max: 1} |

Counts are rough initial estimates and will be calibrated against Duck Creek in Prompt 08. Floors are not thresholds for prose density — a single `negative_cov_rp` segment typically projects tens of norms (one per basket subtype × permission); the min/max refer to the count of *top-level* projected norms attributable to that segment.

**Norm-kind taxonomy coverage.** Every permitted `(modality, action_class_label)` pair that appears in any Duck Creek gold answer must be present in the projected norm set. `covenant_norm_coverage` returns the ratio; threshold is 0.95.

**Ground-truth round-trip.** `app/data/duck_creek_rp_ground_truth.yaml` is a hand-annotated expected norm graph (permissions, prohibitions, capacity compositions, condition trees, defeaters). After v4 projects Duck Creek, `app/services/validation_harness.py` diffs the projected norms against the YAML. Any missing norm, wrong modality, wrong capacity_composition, wrong scope, or missing defeater fails the harness.

### 9.2 Hard classifications

Three classifications are deontic choices that cannot be settled by string match and so get an explicit accuracy measurement (Rule 4.3). The test-deal threshold for moving a classification into production use is 95%.

**capacity_composition** — the closed taxonomy in §4.1. Measurement: human-labelled expected values for every RP basket subtype in Duck Creek (≥ 20 instances per class). Compare projected `capacity_composition` against the label.

**action_scope** (`specific` / `general` / `reallocable`). Measurement: human-labelled scope for every projected permission in Duck Creek. Compare against the label.

**condition_structure** (the shape of the condition tree: which predicates, what operator, correct nesting). Measurement: for each norm with a non-trivial condition in Duck Creek, human-labelled expected tree in the ground-truth YAML. Compare tree-structurally (operator at each node + predicate_label at each leaf), ignoring id-level differences.

`app/services/classification_measurement.py` runs all three and prints per-class precision/recall plus confusion matrices. A classification that fails its threshold must not be consumed by downstream operations until it passes — `intent_parser.py` consults the classification-status table and refuses to dispatch operations that depend on a failing classification.

---

## 10. Directory layout for v4

New and modified files, organized by layer.

**Schema and data**
- `app/data/schema_v4_deontic.tql` — the deontic schema (Section 4)
- `app/data/deontic_condition_functions.tql` — §5.1
- `app/data/deontic_norm_functions.tql` — §5.2
- `app/data/deontic_capacity_functions.tql` — §5.3
- `app/data/deontic_pathway_functions.tql` — §5.4
- `app/data/deontic_validation_functions.tql` — §5.5
- `app/data/deontic_pattern_functions.tql` — §5.6
- `app/data/rp_deontic_mappings.tql` — the projection mapping seed (§8.3)
- `app/data/duck_creek_rp_ground_truth.yaml` — hand-annotated expected norm graph

**Services**
- `app/services/deontic_projection.py` — extraction → norms engine (~200 LOC)
- `app/services/deontic_operations.py` — the 11 operations as Python entry points calling TypeDB functions
- `app/services/intent_parser.py` — natural-language question → `IntentObject` (operation + parameters)
- `app/services/deontic_renderer.py` — operation result → prose
- `app/services/validation_harness.py` — completeness checks (§9.1)
- `app/services/classification_measurement.py` — classification accuracy harness (§9.2)

**Router and init**
- `app/routers/deals.py` — modified: RP endpoints route to the v4 path; MFN/DI/other covenants return a `v4 supports RP only` 501 stub
- `app/scripts/init_schema_v4.py` — v4-specific schema init loading `schema_v4_deontic.tql` + the six `deontic_*_functions.tql` + `rp_deontic_mappings.tql` into the `valence_v4` database

**Tests**
- `tests/test_v4_schema.py` — schema integrity
- `tests/test_v4_projection.py` — every mapping rule round-trips
- `tests/test_v4_operations.py` — each of the 11 operations on a fixture provision
- `tests/test_v4_duck_creek_acceptance.py` — the 6-question acceptance test (§7)
- `tests/test_v4_completeness_harness.py` — §9.1 harnesses
- `tests/test_v4_classification_accuracy.py` — §9.2 harnesses

CLAUDE.md will be updated to reference this file and note the v4 working branch.

---

## 11. What's out of scope for the pilot

- **Pro forma financial math** beyond simple ratio recomputation (i.e., the system will evaluate a supplied ratio against thresholds but will not itself compute pro forma EBITDA or pro forma ratios from raw financial statements).
- **MFN, DI, Liens, Asset Sales, Debt Incurrence, Pro Forma, Investments (as standalone), Intercompany, Prepayments, Amendments, Reporting, and Affiliate Transactions deontic models.** All return the v4-not-implemented stub.
- **Multi-deal comparison.** Cross-deal queries remain v3's responsibility until single-deal v4 is validated.
- **Memo generation (Path B).** The open-ended fallback path is scoped for a later prompt series.
- **Benchmark data.** Aggregate-market-term statistics are not produced by v4.
- **UI changes.** The frontend continues to hit `/api/deals/*` endpoints; v4 is a backend-only refactor for the pilot.
- **Changes to v3 extraction prompts.** v3 extraction stays put; the four surgical additions are new questions, not rewrites (Rule 7.2).

---

## 12. Success criteria

The pilot succeeds when **all of** the following are true on the Duck Creek RP deal:

(a) **Acceptance test passes.** All 6 questions in `lawyer_dc_rp.json` produce answers that substantively match the gold answers — not v3's answers, the gold (Rule 7.4). "Substantive match" = same legal conclusion, same critical numerical values, same section citations, prose may differ.

(b) **Full provenance.** Every v4 answer's citations trace to at least one `norm_extracted_from` anchor and through it to a source_text/source_section on the original extracted entity. No citation is fabricated (Rule 3.3). No user-facing citation refers to an internal `norm_id` (Rule 3.4).

(c) **Zero-synthesis-guidance commitment holds.** No prompt in v4 — neither the extraction prompts (v3's) nor the rendering prompts (new) — contains substantive legal rules (Rule 4.4). A grep for category-level prose guidance in v4 prompt text yields nothing. Renderer prompts describe *how to present a structured result*, not *how to decide what the result should be*.

(d) **Completeness harnesses pass.**
- `covenant_norm_coverage` ≥ 0.95.
- Every seeded segment's norm count is within the expected range.
- Every norm passes `norm_is_structurally_complete` (by construction — storage gate enforces).
- Ground-truth round-trip diffs to empty.

(e) **Classification accuracy thresholds met.** `capacity_composition`, `action_scope`, and `condition_structure` each reach ≥ 95% on the Duck Creek test set.

(f) **Regression tests green.** `tests/test_v4_*` all pass in CI.

Meeting all six unblocks the v4-to-main merge. Missing any one is a blocker and a prompt for the next iteration — not a reason to lower the bar (Rule 7.4).

---

*End of v4 deontic architecture specification.*
