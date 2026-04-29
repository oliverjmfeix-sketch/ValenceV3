# v3 entity inventory + completeness definition (Phase G commit 4)

> Documents the v3 entity inventory currently produced by extraction
> for Duck Creek (`6e76ed06`), categorizes unpopulated schema entities
> as expected-deferred / extraction-gap, and defines what completeness
> would mean for a fully-extracted RP covenant. Define-and-defer per
> Phase G locked scope: no new extraction work in Phase G itself.

## Method

Uses the Phase F commit 2 survey data
(`docs/v4_schema_coherence_audit_data.json`) for instance counts.
Each schema entity type categorized as one of:

- **Extracted** — non-zero instances on Duck Creek
- **Unpopulated-expected** — zero instances; expected by pilot scope
  (covenant not extracted, JCrew pattern not applicable, deferred
  per Phase B/C/D constraints)
- **Unpopulated-gap** — zero instances; should have been extracted
  but wasn't (true gaps to address in extraction work)

## Currently extracted entities (85 of 198 schema types)

### v4 deontic projection infrastructure (Phase C)

These entities populate via Phase C's projection_rule_executor; they
are not directly extracted from the PDF. Stable instance counts
across re-runs.

| Type | Instances | Purpose |
|---|---:|---|
| `projection_rule` | 30 | the rule corpus that emits norms |
| `norm_template` | 25 | norm-emission templates |
| `attribute_emission` | 354 | per-attribute emission specs |
| `value_source` family | 668 (split: 400 string, 158 v3_attr, 45 concat, 45 deal_id, 11 boolean, 9 long) | emission-time value resolution |
| `role_filler` family | 304 (split: 152 emitted_norm + 152 static_lookup) | role-player resolution |
| `role_assignment` | 304 | template→role bindings |
| `relation_template` | 152 | relation-emission templates |
| `entity_type_criterion` | 30 | rule match criteria |
| `attribute_value_criterion` | 11 | rule match criteria |
| `match_criterion` (parent) | 42 (sum of subtypes) | abstract match group |
| `predicate_specifier` | 2 | dynamic-predicate rule specifications |

### v4 deontic emission outputs (per-deal)

These are the v4 outputs of running projection_rule_executor on Duck
Creek.

| Type | Instances | Purpose |
|---|---:|---|
| `norm` | 23 | the canonical v4 deontic facts for Duck Creek |
| `defeater` | 5 | exception/carveout overrides |
| `condition` | 3 (Phase F audit) | deontic condition tree nodes |

### v3 RP covenant entities (extracted from Duck Creek PDF)

These are the v3 entities populated by the Phase D extraction run
($12.95 cost) plus Phase E commit 3's incremental top-up.

| Type | Instances | Purpose |
|---|---:|---|
| `rp_provision` | 1 | the RP covenant root |
| `general_rp_basket` | 1 | $130M / 100% EBITDA general RP |
| `builder_basket` | 1 | Cumulative Amount |
| `ratio_basket` | 1 | 6.06(o) ratio basket |
| `management_equity_basket` | 1 | 6.06(b) management equity |
| `tax_distribution_basket` | 1 | 6.06(c) |
| `holdco_overhead_basket` | 1 | 6.06(k) |
| `equity_award_basket` | 1 | 6.06(d) |
| `unsub_distribution_basket` | 1 | 6.06(p) |
| `general_rdp_basket` | 1 | 6.09(a) general RDP |
| `ratio_rdp_basket` | 1 | ratio-conditioned RDP |
| `builder_rdp_basket` | 1 | builder-style RDP |
| `refinancing_rdp_basket` | 1 | refinancing-only RDP |
| `equity_funded_rdp_basket` | 1 | equity-funded RDP |
| `asset_sale_sweep` | 1 | mandatory-prepayment sweep mechanics (with Phase E commit 3 carveout flags) |
| `sweep_tier` | 3 | leverage-based sweep percentages |
| `investment_pathway` | 6 | J.Crew chain investment pathways |
| `unsub_designation` | 1 | unrestricted-subsidiary designation rules |
| `jcrew_blocker` | 1 | J.Crew designation blocker |
| `intercompany_dividend_permission` | 1 | intercompany permissions |
| `basket_reallocates_to` | 2 (Phase F commit 3 cleanup) | RP↔RDP reallocation directional pairs |

### Ontology + provenance entities

| Type | Instances | Purpose |
|---|---:|---|
| `ontology_category` | 18 (RP-only) | category metadata |
| `ontology_question` | 238 | extraction questions across all covenants |
| `gold_question` | 18 | gold-standard reference questions |
| `expected_norm_kind` | 14 | harness expectation specs |
| `segment_norm_expectation` | 7 | harness segment-level expectations |
| `deontic_mapping` | 15 | v3-to-v4 archive mapping |
| `condition_builder_spec` | 5 | Phase B condition-tree builders |
| `basket_capacity_class` | 34 | basket-type → capacity-class lookup |
| `event_class` | (1) | `asset_sale_event` |
| `state_predicate` | 22 | deontic predicate definitions |
| `classification_field_config` | 3 | per-field classification rules |
| `document_segment_type` | 21 | segment-type taxonomy |

## Unpopulated entities (98 of 198)

### Unpopulated-expected (covenant types not extracted on this deal)

Duck Creek has only RP+MFN+DI extracted. The following entities
populate during MFN, DI, Liens, Investments, Asset Sales,
Fundamental Changes covenant extractions:

- **MFN (deferred — minimal extracted state on Duck Creek):**
  `mfn_provision`, `mfn_exclusion`, `mfn_freebie_basket`,
  `mfn_sunset_provision`, `mfn_yield_definition`, `yield_component`
- **DI (deferred):** `di_provision`, `di_facility_type`,
  `di_lien_priority`, `incremental_facility`, `leverage_tier`,
  `incremental_basket`, `ratio_debt_basket`, `ratio_required_basket`,
  `ratio_secured_basket`, `ratio_unsecured_basket`,
  `ratio_test_type`, `debt_condition_type`, `facility_prong`,
  `general_basket`, `purchase_money_basket`, `working_capital_basket`,
  `lc_basket`, `non_guarantor_basket`, `foreign_subsidiary_basket`,
  `intercompany_basket`, `ied_basket`, `no_default_basket`,
  `refinancing_basket`, `credit_agreement_basket`,
  `acquisition_basket`, `capital_lease_basket`, `contribution_basket`,
  `earnout_basket`, `hedging_basket`, `sale_leaseback_basket`
- **Liens (entire covenant deferred):**
  `lien_priority`, `lien_release_mechanics`
- **Investments / Pathways (entity types beyond what's already
  extracted via investment_pathway):** `investment_provision`,
  `investment_basket`, `investment_basket_type`
- **Other covenant-specific:** `governance_concept`,
  `dividend_definition_element`, `transfer_definition`,
  `transfer_inclusion_type`, `transfer_type`, `material_definition_method`,
  `material_threshold_basis`, `materiality_definition`,
  `transaction_cost_type`, `overhead_cost_type`, `tax_group_type`,
  `equity_award_type`, `repurchase_trigger`, `unsub_distribution_condition`

### Unpopulated-deferred (specific Phase C / pilot deferrals)

| Type | Reason | Revisit trigger |
|---|---|---|
| `general_investment_basket` | Phase C deferred (Duck Creek extraction didn't populate; Q5 gold answer references it) | Re-extraction window for Duck Creek OR new deal extraction |
| `event_instance` | Phase C concession (Rule 5.2 — world state input parameter shape, not stored) | TypeDB 3.x gains parameterized function calls |
| `condition` (declared but unpopulated subtypes) | Some condition subtypes only populate when conditions of that shape exist in extracted data | Future deals with matching condition shapes |
| `attribute_provenance` | Schema declared but extraction doesn't currently produce | Future provenance-tracking phase |

### Unpopulated-infrastructure (declared for symmetry; not blocking)

- **Match-criterion subtypes** that haven't been used by any rule yet:
  `attribute_existence_criterion`, `linked_via_relation_criterion`,
  `subtype_criterion`. Future projection rules may use them.
- **Value-source subtypes** that haven't been used:
  `arithmetic_value_source`, `conditional_value_source`,
  `literal_double_value_source`, `produced_norm_id_value_source`.
  Same — future rules may use them.
- `produced_norm_role_filler` — additional role-filler subtype for
  rules that emit role players that are themselves emitted norms.
  Currently not used.

### Unpopulated-gap (true extraction gaps)

After categorization, the following are TRUE gaps for the RP covenant
specifically (i.e., they should have been extracted but weren't):

- `general_investment_basket` (already documented as Phase C
  deferral; remains the canonical example).
- `amendment_threshold` (RP-relevant; not extracted on Duck Creek).

The other unpopulated types are either covenant-deferred or
infrastructure-pending. Only 2 RP-relevant true gaps surfaced by
this audit. Phase E partially closed Q3/Q4 gaps; remaining gaps
are deferred per Phase G locked scope.

## Completeness definition for RP covenant extraction

**Forward-looking standard.** Future extraction work (next deal,
re-extraction window, etc.) should produce ALL of the following
entity types when they exist in the source agreement. Failure to
populate when the source has the corresponding language is a
genuine extraction-gap finding.

### Required (always populate when source has the concept)

- `rp_provision` (1 per deal)
- All RP basket types: `general_rp_basket`, `builder_basket`,
  `ratio_basket`, `management_equity_basket`, `tax_distribution_basket`,
  `holdco_overhead_basket`, `equity_award_basket`,
  `unsub_distribution_basket`
- All RDP basket types: `general_rdp_basket`, `ratio_rdp_basket`,
  `builder_rdp_basket`, `refinancing_rdp_basket`,
  `equity_funded_rdp_basket`
- `general_investment_basket` (currently Phase C deferred for Duck
  Creek; required for completeness)
- `asset_sale_sweep` with all carveout flags (sweep tiers + de
  minimis + 2.10(c)(iv) flag + 6.05(z) flag + leverage-tier exempts +
  non-collateral / ordinary-course / casualty exempts) — Phase E
  established the schema; future extractions should populate
  consistently
- `sweep_tier` per-leverage-band entries
- `investment_pathway` per source→target type pair (J.Crew chain
  analysis)
- `unsub_designation` (1 per deal)
- `jcrew_blocker` (1 per deal if a J.Crew-style blocker exists)
- `intercompany_dividend_permission` (1 per deal if intercompany
  dividends are explicitly permitted)
- `basket_reallocates_to` per directional reallocation path (Phase F
  commit 1+3 confirmed storage idempotency)

### Conditional (populate when source has the concept; absent
otherwise)

- `event_class` instances beyond `asset_sale_event` (e.g.,
  `debt_issuance_event`, `qualified_ipo_event`, etc.) — populate as
  needed when extraction expands.
- `amendment_threshold` if the source contains amendment-threshold
  language (most RP covenants do).

### Forward-looking (Phase F deferred)

- `event_governed_by_norm` relations (Phase F commit 3 added the
  schema; populating rules are Phase H or post-pilot scope).
- `condition` subtypes for any condition shape extracted (depth-2
  AND/OR currently supported; depth-3 requires Strategy A flattening
  per Phase B audit).

## Items routing to Commit 6 (outcome consolidation)

The completeness definition above is forward-looking. Phase G doesn't
attempt to close any of the listed gaps — that's extraction work,
not synthesis-architecture work. Phase G's scope ends at documenting
the standard. Subsequent phases consume this document when planning
new extraction or re-extraction.

## Items routing to known-gaps

- `general_investment_basket` extraction (already documented in
  multiple known-gaps entries; this audit confirms it's the only
  currently-known RP-relevant true extraction gap on Duck Creek).
- Liens, Investments, Asset Sales, Fundamental Changes, and other
  deferred covenant types: noted as expected-deferred in this
  inventory; covenant-extraction work is post-pilot scope.
