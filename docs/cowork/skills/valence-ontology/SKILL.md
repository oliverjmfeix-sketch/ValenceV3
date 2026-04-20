---
name: valence-ontology
description: Use this skill when the user is authoring or editing Valence covenant ontology files — the `.tql` files in `app/data/` that define `ontology_category`, `ontology_question`, and `category_has_question` rows. Triggers whenever the user mentions "ontology", "new question", "Xtract report", "covenant module", "RP / DI / MFN / Liens / Investments / Asset Sales / EOD / FC / Prepayments / Amendments / Fundamental Changes / Affiliate Tx / Pro Forma / Conditions Precedent", or edits any file matching `app/data/*_ontology_questions.tql`. Do NOT trigger for schema changes (`schema_unified.tql`), Python code, or anything outside `app/data/`.
---

# Valence Ontology Authoring Skill

You are helping a covenant expert author TypeQL ontology rows for the Valence
covenant intelligence platform. The working directory is a clone of the
`valence-backend` repo, and you have file access only to it.

## Absolute rules

1. **Never edit `schema_unified.tql`.** If an author's request would require a
   new entity type, new attribute, or new relation in the schema, stop and
   respond: "This requires a schema change. Drafting the question as a scalar
   with a `# TODO-ENG:` tag — engineering will wire the entity type."

2. **Never edit any `.py` file.** Full stop.

3. **Never rename an existing `question_id`** (the attribute value in
   `has question_id "..."`). Data rows reference these. If a concept is
   obsolete, add a tombstone comment and introduce a new ID.

4. **Never invent a covenant type code.** The valid codes are exactly:
   `RP, DI, MFN, LIENS, INV, AS, EOD, FC, PP, AMD, FUND, AFF, PF, CP`.
   If the user proposes a new covenant module, stop and ask for engineering
   to register the code.

5. **Never push to `main`.** When committing, always use a branch named
   `ontology/<module>-<short-desc>` and open a **draft** PR.

6. **Always run `python app/scripts/validate_ontology.py <file>`** after
   every edit and report the output. If validation fails, fix before
   reporting success.

## Before editing

Before proposing ANY edit:

1. Read `docs/cowork/ONTOLOGY_AUTHOR_GUIDE.md` (once per session is enough —
   cache it).
2. Read the target file in full.
3. Read `app/data/_TEMPLATE_new_covenant.tql` for canonical patterns.
4. If the request mentions a specific covenant already in production, also
   read the existing file (e.g. `di_ontology_questions.tql` for DI-style
   work).

Only then propose edits.

## Output format for new questions

Every new scalar question must be written in exactly this shape:

```tql
insert $<localvar> isa ontology_question,
    has question_id "<prefix>_<letter><number>",
    has covenant_type "<CODE>",
    has question_text "<one sentence, ends with ? for questions>",
    has answer_type "<boolean|integer|number|string>",
    has display_order <int in the module's range>,
    has extraction_prompt "<2-6 sentences: where to look + what phrasings + what makes true/false + output format>";

match $cat isa ontology_category, has category_id "<CAT_ID>";
      $q isa ontology_question, has question_id "<prefix>_<letter><number>";
insert (category: $cat, question: $q) isa category_has_question;
```

### What NOT to do

- Do not emit `plays` or `relates` clauses. Those belong in the schema.
- Do not emit `$q has ...` on a separate line after the insert — use a
  single multi-line `insert` statement with commas.
- Do not emit `has is_required true` unless the user explicitly asks. The
  default is fine.
- Do not use `has description` on questions — that attribute is used on
  categories. For questions, the `question_text` IS the description.
- Do not import anything. The files are bare TypeQL.

### For categories (less frequent)

```tql
insert $<localvar> isa ontology_category,
    has category_id "<CODE><N>",
    has name "<3-6 word title>",
    has description "<one sentence>",
    has display_order <int>,
    has extraction_context_sections "<SEG1,SEG2>",
    has extraction_batch_hint "<one-sentence hint that tells Claude where to focus when batch-extracting this category>";
```

`extraction_context_sections` values come from `app/data/segment_types_seed.tql` — use only segment_type_id values that exist there. Common ones: `DEFINITIONS`, `NEGATIVE_COVENANTS`, `DEBT_INCURRENCE`, `LIENS_COVENANT`, `INVESTMENTS_COVENANT`, `RESTRICTED_PAYMENTS`, `ASSET_SALES`, `FINANCIAL_COVENANTS`, `INCREMENTAL_FACILITY`, `EVENTS_OF_DEFAULT`, `AMENDMENTS_WAIVERS`, `CONDITIONS_PRECEDENT`.

## Writing extraction prompts

A strong extraction prompt has four beats, in order:

1. **Locate.** Where in the agreement to look. Name the covenant (never the
   section number — numbers vary).
2. **Recognize.** Two or three specific drafting phrasings to spot.
3. **Disambiguate.** What would make the answer true vs. false, or what
   would push the extractor off-track.
4. **Format.** The exact output shape (for integers: units; for strings:
   verbatim vs. normalized).

Example, good:

> "Determine whether the Asset Sales covenant contains a 'reinvestment
> right' — a provision allowing the Borrower to retain Net Cash Proceeds
> from asset sales if reinvested in the business within a stated period.
> Look for 'Reinvestment Period', 'within 365 days after receipt', or 'if
> the Borrower intends to reinvest'. Answer true if any reinvestment
> mechanic exists, even if limited. Do not answer true if the ability is
> only to contract to reinvest (i.e., signing a commitment) — the question
> is about whether proceeds can be retained pending reinvestment. Return
> as boolean."

Example, too thin (fix before emitting):

> "Is there a reinvestment period?"

## Handling entity-list requests

When the user says "extract all X" (baskets, tiers, exceptions, pathways),
that's usually an entity-list question. Don't write one directly. Instead:

1. Check whether a matching `entity <type>` already exists in
   `schema_unified.tql` (you can read it — just never edit).
2. If it exists, draft the question with `has answer_type "entity_list"`
   and `has target_entity_type "<type>"` and `has target_relation_type
   "<relation>"`. Verify both exist in the schema before writing.
3. If it doesn't exist, write a `# TODO-ENG:` block with the proposed entity
   shape and stop. Do not write the `ontology_question` row — engineering
   will add the schema type and then you can come back and write it.

## Cross-references to other covenants

Credit agreements cross-reference extensively. If a new question touches
another covenant (e.g., "does the debt incurrence basket correlate to a
lien basket of the same size?"), use the existing covenant codes and
reference existing question IDs in the extraction prompt as natural
language ("verify against the Liens covenant"). Do not create formal
cross-reference rows — those are managed by engineering via
`seed_cross_covenant_mappings.tql`.

## Commit / PR flow

When the user says "commit this" or "open a PR":

1. Create a branch: `git checkout -b ontology/<module>-<description>`.
2. Stage only the `.tql` files you edited in this session.
3. Commit with message format:
   ```
   ontology(<module>): <short description>

   - Added <N> questions to <category/categories>
   - <any TODO-ENG flags raised>
   - Validator: PASS
   ```
4. Push branch.
5. Open **draft** PR (never ready-for-review) with body:
   ```
   ## Summary
   <what was added>

   ## Validator
   <paste validator output>

   ## TODO-ENG
   <list any schema changes engineering must make before merge>

   ## Source
   <which Xtract report or credit agreement inspired these questions>
   ```
6. Tag `@valence-eng` for review.

Never click "Ready for review" yourself. The human author does that.

## When to stop and ask

Stop and ask the user before proceeding if:

- The request implies a schema change (new entity type, new attribute, new
  relation).
- The request uses a covenant code you don't recognize.
- The request would produce more than 10 questions in a single response —
  offer to split into reviewable batches.
- The validator returns errors you can't fix in one pass.
- You're uncertain whether a behavior is scalar or entity-list.

## File map (for your reference)

- `app/data/questions.tql` — RP questions (historical single-letter cats A-Z)
- `app/data/categories.tql` — RP category definitions + RP linkage relations
- `app/data/mfn_ontology_questions.tql` — MFN self-contained (cats + Qs + links)
- `app/data/di_ontology_questions.tql` — DI self-contained (cats + Qs + links)
- `app/data/liens_ontology_questions.tql` — Liens (this kit adds this file)
- `app/data/investments_ontology_questions.tql` — Investments
- `app/data/asset_sales_ontology_questions.tql` — Asset Sales
- `app/data/eod_ontology_questions.tql` — Events of Default
- `app/data/fincov_ontology_questions.tql` — Financial Covenants
- `app/data/prepayments_ontology_questions.tql` — Prepayments / ECF
- `app/data/amendments_ontology_questions.tql` — Amendments & Waivers
- `app/data/fundamental_changes_ontology_questions.tql` — Fundamental Changes
- `app/data/affiliate_tx_ontology_questions.tql` — Affiliate Transactions
- `app/data/pro_forma_ontology_questions.tql` — Pro Forma Mechanics
- `app/data/conditions_precedent_ontology_questions.tql` — Conditions Precedent
- `app/data/_TEMPLATE_new_covenant.tql` — canonical template, DO NOT MODIFY

For each post-RP module, the file contains categories + questions + linkage
in one file, following the `di_ontology_questions.tql` pattern (not the
split RP pattern, which is historical).

## TypeDB 3.x syntax reminders

Valence uses TypeDB 3.x. Common mistakes to avoid:

- `has X "string"` for string attrs (quoted).
- `has X 123` for integer attrs (no quotes).
- `has X true` / `has X false` for boolean attrs (no quotes, lowercase).
- Ends every `insert` with `;`.
- `match ... ; insert ...` for link relations (two statements, one block).
- No 2.x keywords: `get`, `?value`, `rule`, `sub entity`. If you see any of
  these, you're doing it wrong.

If in doubt, consult `typedb_3x_reference.md` in the project root.
