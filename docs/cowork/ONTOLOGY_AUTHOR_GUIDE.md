# Valence Ontology Authoring Guide

This is the working handbook. Keep it open in a side window during Cowork
sessions. It covers:

1. What you're actually authoring
2. The anatomy of a good question
3. How to write extraction prompts that extract well
4. The three question shapes (scalar, multiselect-flagged-for-ENG, entity-list-flagged-for-ENG)
5. Naming and numbering conventions
6. The Xtract-to-gap workflow
7. Anti-patterns that cause extraction failures

---

## 1. What you're authoring

You are building the **question set** that Valence asks of every uploaded
credit agreement. Each question produces one typed primitive (boolean,
integer, number, string) with full provenance back to the source PDF.

Your output is plain text in a `.tql` file — specifically, two kinds of row:

```
insert $<var> isa ontology_category,
    has category_id "XYZ",
    has name "...",
    has description "...",
    has display_order 123,
    has extraction_context_sections "SECTION_A,SECTION_B",
    has extraction_batch_hint "...";
```

and:

```
insert $<var> isa ontology_question,
    has question_id "xyz_a1",
    has covenant_type "XYZ",
    has question_text "...",
    has answer_type "boolean",
    has display_order 456,
    has extraction_prompt "...";

match $cat isa ontology_category, has category_id "XYZ1";
      $q isa ontology_question, has question_id "xyz_a1";
insert (category: $cat, question: $q) isa category_has_question;
```

You never write SQL. You never write Python. You never define new entity
types. If you need one, mark it `# TODO-ENG:` and engineering handles it.

---

## 2. Anatomy of a good question

A good Valence question is:

- **Atomic.** One thing being asked. Not "does X exist *and* what's the
  threshold?" — that's two questions.
- **Self-contained.** The extraction prompt must make sense to someone
  (Claude) reading only that question, without other context.
- **Answerable from the document.** No questions that require market data,
  comparables, or information outside the four corners of the agreement.
- **Consistent with existing terminology.** If the codebase already uses
  "Incremental Equivalent Debt", don't switch to "Sidecar Debt."
- **Section-agnostic.** Never hardcode "Section 6.01". Use "the debt
  incurrence covenant" or "the relevant section." Section numbering varies
  between agreements.

Good:

```
question_text: "Is there a no-default condition on the general debt basket?"
answer_type: "boolean"
extraction_prompt: "Determine whether the general debt basket (a
    fixed-dollar-or-grower permitted debt basket not tied to a leverage
    ratio) is conditioned on the absence of a Default or Event of Default at
    the time of incurrence. Look for language within the basket clause such
    as 'so long as no Event of Default has occurred and is continuing' or a
    cross-reference to a Section 6.02-style conditions block. Answer true
    only if the condition explicitly applies to THIS basket — not a
    different basket elsewhere in the covenant."
```

Bad:

```
question_text: "Debt basket default conditions and materiality?"
answer_type: "string"
extraction_prompt: "Find out about default conditions."
```

The bad version fails on all five criteria: two things in one, vague
prompt, string answer where boolean would work, hardcodes nothing useful,
and won't produce a comparable primitive across deals.

---

## 3. Writing extraction prompts that actually extract

An extraction prompt is a directive that Claude uses when reading the PDF.
The engine passes your `question_text` + `extraction_prompt` to Claude with
the relevant document sections already pre-sliced. Your prompt should:

### Tell Claude where to look

> "Look in the Asset Sales covenant (may be titled 'Dispositions' in some
> agreements) for any restriction on the use of proceeds from the sale of
> non-Collateral assets..."

### Tell Claude what specific language to expect

Give 2–3 typical phrasings. Credit agreements share drafting conventions;
naming the phrasings helps Claude recognize them fast.

> "Common phrasings include 'Net Cash Proceeds shall be applied...' or
> 'the Borrower shall prepay the Term Loans with 100% of Net Cash
> Proceeds...'"

### Tell Claude what would make this true vs. false

Especially for booleans. Ambiguity in the question → ambiguity in the
answer → downstream wrong inferences.

> "Answer true if *any* mandatory prepayment from asset sales exists, even
> if it only applies above a de minimis threshold. Answer false only if
> there is no mandatory prepayment language at all. Do not answer false
> just because there are exceptions — exceptions are captured by other
> questions."

### Tell Claude the output format

For integers and numbers, always state units.

> "Return as an integer in basis points (e.g., 50 for 50bps, not 0.005)."

> "Return in USD millions as a number (e.g., 130.0 for $130 million). If the
> amount is stated as 'the greater of $X and Y% of EBITDA', return only the
> dollar amount here; the EBITDA grower percentage has its own question."

### Never hardcode section numbers

US-style agreements number differently from UK. Even among US agreements,
6.01 vs. 7.01 vs. 7.02 varies. Say "the debt incurrence negative covenant"
and let Claude find it.

### Length: 2–6 sentences

A prompt under two sentences usually underspecifies. Over six and you're
duplicating schema documentation. Aim for the sweet spot.

---

## 4. The three question shapes

### 4a. Scalar (the 90% case — you own this completely)

`answer_type` is `"boolean"`, `"integer"`, `"number"`, or `"string"`.

Each produces one primitive. Stored via `provision_has_answer` and
optionally routed to an entity attribute via `question_annotates_attribute`
(engineering writes the annotation; you just write the question).

### 4b. Multiselect (rare — draft and flag)

`answer_type` is `"multiselect"`. Claude returns a list of concept IDs from a
closed set. Used when the same thing could take 1 of N or M of N values from
a predefined list (e.g., "which leverage test types apply?").

You draft the question and list the options in a comment. Engineering wires
the `concept` entities.

```
# TODO-ENG: Multiselect question. Options to wire as `concept` entities:
#   - test_total_leverage
#   - test_first_lien_leverage
#   - test_secured_leverage
#   - test_interest_coverage
#   - test_fixed_charge_coverage
insert $q isa ontology_question,
    has question_id "fc_a3",
    has covenant_type "FC",
    has question_text "Which leverage/coverage tests apply as maintenance covenants?",
    has answer_type "multiselect",
    has display_order 1403,
    has extraction_prompt "...";
```

### 4c. Entity-list (variable cardinality — draft and flag)

`answer_type` is `"entity_list"`. Claude returns an array of JSON objects,
one per instance, each becoming a typed entity in TypeDB. Used when there
can be N distinct items (basket tiers, sweep tiers, leverage tiers,
exceptions, pathways).

You draft the extraction prompt and declare the target entity shape in a
comment. Engineering adds the entity type to the schema.

```
# TODO-ENG: entity_list question. Proposed entity shape (as comment for ENG):
#   entity financial_covenant_test,
#     owns test_type (multiselect: leverage/coverage/liquidity),
#     owns numerator_definition,
#     owns denominator_definition,
#     owns threshold_value,
#     owns step_down_dates (entity_list of its own),
#     owns equity_cure_permitted (bool)
insert $q isa ontology_question,
    has question_id "fc_el_tests",
    has covenant_type "FC",
    has question_text "Extract all maintenance financial covenant tests.",
    has answer_type "entity_list",
    has display_order 1490,
    has extraction_prompt "...";
```

---

## 5. Naming and numbering

### Covenant type codes

Already assigned — **do not change**:

| Covenant                 | Code   | Question ID prefix | Category ID prefix |
|--------------------------|--------|--------------------|--------------------|
| Restricted Payments      | `RP`   | `rp_`              | `A`, `B`, `C`, ... (single letters, historical) |
| Debt Incurrence          | `DI`   | `di_`              | `DI1`–`DI12`       |
| MFN                      | `MFN`  | `mfn_`             | `MFN1`–`MFN6`      |
| Liens                    | `LIENS`| `ln_`              | `LN1`–`LN8`        |
| Investments              | `INV`  | `inv_`             | `INV1`–`INV8`      |
| Asset Sales              | `AS`   | `as_`              | `AS1`–`AS9`        |
| Events of Default        | `EOD`  | `eod_`             | `EOD1`–`EOD8`      |
| Financial Covenants      | `FC`   | `fc_`              | `FC1`–`FC8`        |
| Prepayments / ECF        | `PP`   | `pp_`              | `PP1`–`PP6`        |
| Amendments               | `AMD`  | `amd_`             | `AMD1`–`AMD6`      |
| Fundamental Changes      | `FUND` | `fund_`            | `FUND1`–`FUND5`    |
| Affiliate Transactions   | `AFF`  | `aff_`             | `AFF1`–`AFF4`      |
| Pro Forma Mechanics      | `PF`   | `pf_`              | `PF1`–`PF4`        |
| Conditions Precedent     | `CP`   | `cp_`              | `CP1`–`CP4`        |

### Question IDs

- Lowercase snake_case.
- Format: `<prefix>_<category_letter><number>` — e.g., `ln_a1`, `ln_a2`, `ln_b1`.
- Use `<prefix>_el_<name>` for entity-list questions: `fc_el_tests`,
  `as_el_sweep_tiers`.
- **Never rename a question ID once it's been merged.** Downstream
  annotations and data rows reference them. If a question becomes obsolete,
  leave a tombstone comment (`# rp_xyz REMOVED — replaced by ...`) and add
  the new one with a new ID.

### display_order ranges (per covenant module)

| Covenant            | Category display_order | Question display_order |
|---------------------|-----------------------|------------------------|
| RP                  | 1–14                  | 1–180                  |
| MFN                 | 101–106               | 1–90                   |
| DI                  | 201–212               | 1–179                  |
| Liens               | 301–308               | 1000–1099              |
| Investments         | 401–408               | 1100–1199              |
| Asset Sales         | 501–509               | 1200–1299              |
| Events of Default   | 601–608               | 1300–1399              |
| Financial Covenants | 701–708               | 1400–1499              |
| Prepayments / ECF   | 801–806               | 1500–1599              |
| Amendments          | 901–906               | 1600–1699              |
| Fundamental Changes | 1101–1105             | 1800–1899              |
| Affiliate Transactions | 1001–1004          | 1700–1799              |
| Pro Forma Mechanics | 1201–1204             | 1900–1999              |
| Conditions Precedent| 1301–1304             | 2000–2099              |

The validator enforces that question `display_order` falls inside the right
range for its covenant type. If you see an "out of range" error, check the
prefix and the numbering.

---

## 6. The Xtract-to-gap workflow

When a new Xtract Research report lands, the job is to ask: *what did the
report cover that Valence can't answer yet?*

Recommended Cowork prompt pattern:

> Read the Xtract report at `<path>.pdf`. Then read
> `app/data/questions.tql` and `app/data/di_ontology_questions.tql`. For every
> factual claim in the Xtract report that Valence's current question set
> cannot produce, draft a new question. Return a numbered list of proposed
> questions with:
>
> 1. The exact Xtract sentence it covers (quoted with page #).
> 2. The proposed question_id, category_id, question_text, answer_type, and
>    extraction_prompt.
> 3. Any TODO-ENG flags if it would need a new entity type.
>
> Do not insert anything into a .tql file yet. Produce the list first; I will
> review and then tell you which ones to insert.

This review step is the whole value of keeping a human in the loop. Don't
skip it. Expect 40–60% of the draft questions to need revision before they
belong in the ontology.

---

## 7. Anti-patterns

### 7a. "Does [multiple things] exist?"

Split. Always.

### 7b. String answer where a boolean or integer would work

If it can be coerced to a boolean ("is X the case?") or a number ("what's
the threshold?"), use that type. Strings are for section references and
genuinely narrative answers. Analysts will thank you downstream when they
can filter and compare.

### 7c. Reusing a question_id with a different meaning

The question IDs are keyed in TypeDB. Once data references them, you cannot
silently change the meaning. If the concept has evolved, add a new question
with a new ID.

### 7d. Extraction prompts that describe the answer instead of how to find it

> ❌ "The MFN threshold is 50bps in most agreements."

That's a market comment, not an extraction instruction.

> ✅ "Extract the MFN threshold in basis points. Look for language like
> 'exceeds the Applicable Rate by more than [X] basis points'. Return as
> integer."

### 7e. Questions that depend on Valence's previous answers

Every question is answered independently against the raw document. If you
need to ask "given the threshold is 50bps, how does X..." — that's a
synthesis question, handled by the graph layer, not the extraction layer.
Flag and discuss with engineering.

### 7f. Inventing new entity types without engineering

If it needs a new type, write `# TODO-ENG:` and stop. The validator will
catch it if you forget.

---

## 8. When in doubt

Look at what DI and MFN do. They're the two most mature modules after RP,
they follow the current schema conventions, and they've been battle-tested
against the Xtract gold standard. When the template shows a pattern, that
pattern is what production looks like.

And: ask. A three-line Slack to engineering saves an hour of rework.
