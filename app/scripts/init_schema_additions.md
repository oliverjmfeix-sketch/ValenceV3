# `init_schema.py` additions — wiring new ontology files

This doc tells engineering exactly what to add to
`app/scripts/init_schema.py` so the 11 new covenant modules are loaded by
`python -m app.scripts.init_schema` along with the existing RP / MFN / DI
data.

**Owner:** Engineering (Python change, not a covenant-expert task).

**Time to apply:** ~5 minutes.

---

## The pattern

Each new `<module>_ontology_questions.tql` file follows the same format as
`di_ontology_questions.tql` — one file with categories + questions +
linkage. Loading uses the existing `_load_mixed_tql_file` helper; no new
helper code is needed.

Add each file as a new step in the main loader function, following steps
16b/16c/16d that load the DI files. The numbering gets awkward; the cleanest
placement is a new block of contiguous steps (we'll use 16e through 16o)
inserted immediately after the DI load steps and before step 17 (synthesis
guidance).

---

## Step 1 — Add the file constants

In the constants block near the top of `init_schema.py` (where
`DI_QUESTIONS_FILE`, `DI_ANNOTATIONS_FILE`, etc. are declared), add:

```python
# New covenant modules added via the Cowork Ontology Authoring Kit
LIENS_QUESTIONS_FILE      = DATA_DIR / "liens_ontology_questions.tql"
INVESTMENTS_QUESTIONS_FILE = DATA_DIR / "investments_ontology_questions.tql"
ASSET_SALES_QUESTIONS_FILE = DATA_DIR / "asset_sales_ontology_questions.tql"
EOD_QUESTIONS_FILE         = DATA_DIR / "eod_ontology_questions.tql"
FINCOV_QUESTIONS_FILE      = DATA_DIR / "fincov_ontology_questions.tql"
PREPAYMENTS_QUESTIONS_FILE = DATA_DIR / "prepayments_ontology_questions.tql"
AMENDMENTS_QUESTIONS_FILE  = DATA_DIR / "amendments_ontology_questions.tql"
FUND_CHANGES_QUESTIONS_FILE = DATA_DIR / "fundamental_changes_ontology_questions.tql"
AFFILIATE_TX_QUESTIONS_FILE = DATA_DIR / "affiliate_tx_ontology_questions.tql"
PRO_FORMA_QUESTIONS_FILE    = DATA_DIR / "pro_forma_ontology_questions.tql"
CONDITIONS_PRECEDENT_QUESTIONS_FILE = DATA_DIR / "conditions_precedent_ontology_questions.tql"
```

---

## Step 2 — Add the load steps

In the main loader function, locate the block that ends with:

```python
        # 16d. Load DI entity-list questions
        logger.info("\n16d. Loading seed_di_entity_list_questions.tql...")
        if DI_ENTITY_LIST_QUESTIONS_FILE.exists():
            _load_mixed_tql_file(driver, TYPEDB_DATABASE, DI_ENTITY_LIST_QUESTIONS_FILE)
```

Insert immediately after it (before step 17):

```python
        # ─── Cowork Ontology Authoring Kit — new covenant modules ────────────

        # 16e. Load Liens ontology
        logger.info("\n16e. Loading liens_ontology_questions.tql...")
        if LIENS_QUESTIONS_FILE.exists():
            _load_mixed_tql_file(driver, TYPEDB_DATABASE, LIENS_QUESTIONS_FILE)

        # 16f. Load Investments ontology
        logger.info("\n16f. Loading investments_ontology_questions.tql...")
        if INVESTMENTS_QUESTIONS_FILE.exists():
            _load_mixed_tql_file(driver, TYPEDB_DATABASE, INVESTMENTS_QUESTIONS_FILE)

        # 16g. Load Asset Sales ontology
        logger.info("\n16g. Loading asset_sales_ontology_questions.tql...")
        if ASSET_SALES_QUESTIONS_FILE.exists():
            _load_mixed_tql_file(driver, TYPEDB_DATABASE, ASSET_SALES_QUESTIONS_FILE)

        # 16h. Load Events of Default ontology
        logger.info("\n16h. Loading eod_ontology_questions.tql...")
        if EOD_QUESTIONS_FILE.exists():
            _load_mixed_tql_file(driver, TYPEDB_DATABASE, EOD_QUESTIONS_FILE)

        # 16i. Load Financial Covenants ontology
        logger.info("\n16i. Loading fincov_ontology_questions.tql...")
        if FINCOV_QUESTIONS_FILE.exists():
            _load_mixed_tql_file(driver, TYPEDB_DATABASE, FINCOV_QUESTIONS_FILE)

        # 16j. Load Prepayments / ECF ontology
        logger.info("\n16j. Loading prepayments_ontology_questions.tql...")
        if PREPAYMENTS_QUESTIONS_FILE.exists():
            _load_mixed_tql_file(driver, TYPEDB_DATABASE, PREPAYMENTS_QUESTIONS_FILE)

        # 16k. Load Amendments ontology
        logger.info("\n16k. Loading amendments_ontology_questions.tql...")
        if AMENDMENTS_QUESTIONS_FILE.exists():
            _load_mixed_tql_file(driver, TYPEDB_DATABASE, AMENDMENTS_QUESTIONS_FILE)

        # 16l. Load Fundamental Changes ontology
        logger.info("\n16l. Loading fundamental_changes_ontology_questions.tql...")
        if FUND_CHANGES_QUESTIONS_FILE.exists():
            _load_mixed_tql_file(driver, TYPEDB_DATABASE, FUND_CHANGES_QUESTIONS_FILE)

        # 16m. Load Affiliate Transactions ontology
        logger.info("\n16m. Loading affiliate_tx_ontology_questions.tql...")
        if AFFILIATE_TX_QUESTIONS_FILE.exists():
            _load_mixed_tql_file(driver, TYPEDB_DATABASE, AFFILIATE_TX_QUESTIONS_FILE)

        # 16n. Load Pro Forma Mechanics ontology
        logger.info("\n16n. Loading pro_forma_ontology_questions.tql...")
        if PRO_FORMA_QUESTIONS_FILE.exists():
            _load_mixed_tql_file(driver, TYPEDB_DATABASE, PRO_FORMA_QUESTIONS_FILE)

        # 16o. Load Conditions Precedent ontology
        logger.info("\n16o. Loading conditions_precedent_ontology_questions.tql...")
        if CONDITIONS_PRECEDENT_QUESTIONS_FILE.exists():
            _load_mixed_tql_file(driver, TYPEDB_DATABASE, CONDITIONS_PRECEDENT_QUESTIONS_FILE)

        # ─── End Cowork kit additions ────────────────────────────────────────
```

---

## Step 3 — Run and verify

```bash
python -m app.scripts.init_schema
```

Expected console output snippet:

```
16e. Loading liens_ontology_questions.tql...
   Loaded 25 categories + questions (N chars)
16f. Loading investments_ontology_questions.tql...
   Loaded ... (N chars)
... (through 16o)
17. Loading seed_synthesis_guidance.tql...
```

Then verify with a TypeDB read — either via `/api/debug/schema-check` or a
direct query:

```python
match
    $q isa ontology_question, has covenant_type $ct;
reduce $count = count groupby $ct;
```

Expected: 11 covenant types listed beyond RP/MFN/DI, with the question
counts from `app/data/README.md`.

---

## What's intentionally NOT in this patch

1. **No schema additions.** Every question in this kit is a scalar
   (boolean/integer/number/string). They all slot into the existing
   `ontology_question` entity and the existing `provision_has_answer`
   relation with no schema changes.

2. **No annotation files.** Each module's file ends with a `# TODO-ENG:`
   block that proposes the entity types and annotations needed to convert
   per-basket boolean questions into structured entity_list questions. Those
   are follow-on PRs, one per module, owned by engineering. This initial
   load brings up the scalar question layer first (fastest path to
   incremental extraction coverage).

3. **No `covenant_type` whitelist updates.** The application code reads
   covenant types dynamically from TypeDB — no hardcoded lists need
   updating. If any hardcoded list does exist (check `app/routers/`), add
   the 11 new codes: `LIENS INV AS EOD FC PP AMD FUND AFF PF CP`.

4. **No extraction-service changes.** The service reads questions by
   `covenant_type` from TypeDB. As long as the document segmenter assigns
   segment types matching the `extraction_context_sections` attribute on
   each category, the new questions flow through the existing pipeline.
   Verify `segment_types_seed.tql` covers the segment IDs referenced in the
   new categories — if any are missing, add them.

---

## Segment type verification

The new category rows reference these `extraction_context_sections` values
(comma-separated in the attribute). Verify each appears in
`segment_types_seed.tql` as a `segment_type_id`:

```
AMENDMENTS_WAIVERS          (Amendments covenant)
ASSET_SALES                 (Asset Sales covenant)
CONDITIONS_PRECEDENT        (CP article)
DEFINITIONS                 (Definitions article)
DEBT_INCURRENCE             (DI covenant)
EVENTS_OF_DEFAULT           (EOD article)
FINANCIAL_COVENANTS         (FinCov section)
INCREMENTAL_FACILITY        (Incremental facility section)
INVESTMENTS_COVENANT        (Investments covenant)
LIENS_COVENANT              (Liens covenant)
NEGATIVE_COVENANTS          (Negative Covenants article)
RESTRICTED_PAYMENTS         (RP covenant)
```

If any are missing from `segment_types_seed.tql`, either add them there
(engineering) or search-replace the `.tql` files in this kit to use existing
segment ids. A missing segment id doesn't break loading — it just means the
extraction engine won't pre-slice the right document region for those
questions, so they'll be answered against the whole document (slower,
lower-accuracy).
