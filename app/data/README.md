# Valence Ontology — `app/data/` directory map

Each covenant module has its own `<module>_ontology_questions.tql` file
containing three sections in order: categories, questions, and
`category_has_question` linkage rows. The split follows the
`di_ontology_questions.tql` pattern; the RP module uses a historical split
(`questions.tql` + `categories.tql`) that is retained for compatibility but
not followed for new modules.

## Production modules

| File                                    | Covenant                  | Type   | Qs  | Cats |
|-----------------------------------------|---------------------------|--------|-----|------|
| `questions.tql`                         | Restricted Payments (RP)  | `RP`   | ~70 | A–Z  |
| `categories.tql`                        | RP category defs + links  | `RP`   | —   | —    |
| `mfn_ontology_questions.tql`            | MFN                       | `MFN`  | ~42 | 6    |
| `di_ontology_questions.tql`             | Debt Incurrence           | `DI`   | ~120| 12   |

## Kit additions (this PR)

| File                                        | Covenant              | Type   | Qs | Cats |
|---------------------------------------------|-----------------------|--------|----|------|
| `liens_ontology_questions.tql`              | Liens                 | `LIENS`| 25 | 8    |
| `investments_ontology_questions.tql`        | Investments           | `INV`  | 25 | 8    |
| `asset_sales_ontology_questions.tql`        | Asset Sales           | `AS`   | 26 | 9    |
| `eod_ontology_questions.tql`                | Events of Default     | `EOD`  | 21 | 8    |
| `fincov_ontology_questions.tql`             | Financial Covenants   | `FC`   | 20 | 8    |
| `prepayments_ontology_questions.tql`        | Prepayments / ECF     | `PP`   | 18 | 6    |
| `amendments_ontology_questions.tql`         | Amendments & Waivers  | `AMD`  | 16 | 6    |
| `fundamental_changes_ontology_questions.tql`| Fundamental Changes   | `FUND` | 12 | 5    |
| `affiliate_tx_ontology_questions.tql`       | Affiliate Transactions| `AFF`  | 10 | 4    |
| `pro_forma_ontology_questions.tql`          | Pro Forma Mechanics   | `PF`   | 10 | 4    |
| `conditions_precedent_ontology_questions.tql`| Conditions Precedent | `CP`   | 10 | 4    |
| **Total added**                             |                       |        |**193**|**70**|

## Reference fixtures

| File                           | Purpose                             |
|--------------------------------|-------------------------------------|
| `_TEMPLATE_new_covenant.tql`   | Canonical pattern for new modules.  |
|                                | Marked `@ontology-template` — the   |
|                                | validator skips it by default.      |

## Supporting seed files (already in repo — engineering)

| File                                 | Purpose                            |
|--------------------------------------|------------------------------------|
| `schema_unified.tql`                 | TypeDB schema (entities, relations)|
| `concepts.tql`                       | Multiselect concept types          |
| `segment_types_seed.tql`             | Document segment IDs used in       |
|                                      | `extraction_context_sections`      |
| `seed_new_questions.tql`             | Extra RP questions (B0, N0, etc.)  |
| `seed_entity_list_questions.tql`     | RP entity-list questions           |
| `seed_mfn_entity_list_questions.tql` | MFN entity-list questions          |
| `seed_di_entity_list_questions.tql`  | DI entity-list questions           |
| `seed_di_reference_entities.tql`     | DI reference data                  |
| `seed_cross_covenant_mappings.tql`   | Cross-covenant relations           |
| `seed_capacity_classifications.tql`  | Capacity-category seed             |
| `question_annotations.tql`           | RP scalar → attribute routing      |
| `seed_di_annotations.tql`            | DI scalar → attribute routing      |
| `seed_mfn_annotations.tql`           | MFN scalar → attribute routing     |
| `seed_concept_entity_mapping.tql`    | Multiselect → entity boolean       |
| `seed_synthesis_guidance.tql`        | Category-specific analysis rules   |
| `jcrew_concepts_seed.tql`            | J.Crew pattern concepts            |
| `jcrew_questions_seed.tql`           | J.Crew questions                   |
| `annotation_functions.tql`           | TypeDB 3.x functions (SCHEMA)      |
| `di_functions.tql`                   | DI-specific functions (SCHEMA)     |

## Covenant type code reference

Validator-enforced. A question's `covenant_type` must be one of:

`RP` · `DI` · `MFN` · `LIENS` · `INV` · `AS` · `EOD` · `FC` · `PP` · `AMD` ·
`FUND` · `AFF` · `PF` · `CP`

## Display order ranges

The validator enforces that every question's `display_order` falls in its
module's range. Reserved ranges:

| Module | Question range | Category range |
|--------|----------------|----------------|
| RP     | 1–200          | 0–20           |
| MFN    | 1–100          | 101–110        |
| DI     | 1–200          | 200–220        |
| LIENS  | 1000–1099      | 301–320        |
| INV    | 1100–1199      | 401–420        |
| AS     | 1200–1299      | 501–520        |
| EOD    | 1300–1399      | 601–620        |
| FC     | 1400–1499      | 701–720        |
| PP     | 1500–1599      | 801–820        |
| AMD    | 1600–1699      | 901–920        |
| AFF    | 1700–1799      | 1001–1020      |
| FUND   | 1800–1899      | 1101–1120      |
| PF     | 1900–1999      | 1201–1220      |
| CP     | 2000–2099      | 1301–1320      |

## Running the validator

From the repository root:

```bash
# Validate a single file
python app/scripts/validate_ontology.py app/data/liens_ontology_questions.tql

# Validate everything at once
python app/scripts/validate_ontology.py --all

# JSON output (for CI)
python app/scripts/validate_ontology.py --all --json
```

Exit code 0 = clean. Exit code 1 = errors present. Warnings do not affect
the exit code but should be reviewed.

## Where entity-list questions live

Each module's file ends with a commented `# TODO-ENG:` block describing the
proposed entity types needed to convert its per-basket boolean questions
into a single `entity_list` question. Engineering adds the entity types to
`schema_unified.tql`, then the covenant expert (or engineer) uncomments the
relevant block and re-runs `init_schema`. This is the one-way ratchet
pattern: scalar questions first (ship value fast), entity-list questions
second (structured reporting).
