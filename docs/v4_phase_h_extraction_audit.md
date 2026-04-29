# Extraction methodology audit (Phase H commit 2)

> Method 1 (broad) audit of extraction discipline across 238
> ontology_questions on `valence_v4`. Each finding categorized:
> Aligned / Compounding fix / Non-compounding finding / SSoT
> violation / Convention violation. Survey snapshot:
> `docs/v4_phase_h_extraction_survey/snapshot_20260429T152608Z.json`
> (re-runnable via `app/scripts/phase_h_extraction_survey.py`).

## Headline findings

| Dimension | Aligned | Compounding fix | Non-compounding | SSoT viol. | Conv. viol. |
|---|---:|---:|---:|---:|---:|
| 1. Question authoring | ✓ mostly | 1 | — | — | — |
| 2. Prompt content structure | ✓ mostly | 2 | — | — | — |
| 3. Value convention enforcement | ✗ | 3 | 5 (specific qids) | — | systemic |
| 4. Question→target traceability | ✓ | — | — | — | — |
| 5. Versioning discipline | ✗ | — | — | post-pilot | — |
| 6. Universe handling | ✗ | — | — | post-pilot | — |
| 7. Storage interface coherence | ✓ | — | — | — | — |
| 8. Category alignment | ✓ mostly | 1 | — | — | — |

**Compounding fixes total: 7.** Within the ≤15 cap; well below ≤5
per-commit limit.

## Dimension 1 — Question authoring discipline

**Aligned.** All 238 questions use the same `ontology_question`
entity type with the same canonical attributes (`question_id @key`,
`question_text`, `answer_type`, `extraction_prompt`,
`covenant_type`, `display_order`). All 4 entity_list questions
follow the same shape (with additional `target_entity_type` +
`target_relation_type` attrs).

**Compounding fix #1: 117/238 questions (49%) have empty
`extraction_prompt`.** Distribution by prefix:

| Prefix (category) | Empty prompt count |
|---|---:|
| rp_t (RDP baskets) | 21 |
| rp_h (HoldCo overhead) | 14 |
| rp_f (Builder) | 12 |
| rp_k (J.Crew) | 11 |
| rp_c, rp_e, rp_g | 9 each |
| rp_d, rp_i | 6 each |
| rp_j | 5 |
| ...other prefixes | 3 or fewer |

These questions were authored as data shapes (question_text +
answer_type + display_order) without active LLM extraction prompts.
They likely populate via concept_applicability paths or are
pre-populated during seeding rather than runtime extraction. The
authoring path "scalar with empty prompt = use defaults / skip
runtime" is undocumented but compounding because future authoring
should know whether empty-prompt is a deliberate choice or
oversight.

**Action:** Document the empty-prompt design choice in the
extraction methodology architecture (Commit 6's outcome doc).
Add a schema attribute or convention to make the choice explicit
on the question itself if the validation utility (Commit 3)
queries it; otherwise documentation-only.

**Decision:** documentation-only (no schema attr). The Commit 3
validation utility flags missing required attrs, but treating
empty-prompt as missing-required would mass-fail the existing
seed; better to document the convention.

## Dimension 2 — Prompt content structure

**Aligned mostly.** When prompts ARE non-empty (121/238 = 51%),
they share an informal structure:
- Domain-specific instructions (where to look in the agreement)
- Pattern hints ("Often structured as 'greater of $X and Y%'")
- Sometimes explicit output format ("answer 0.20", "answer null")

**Compounding fix #2: prompt-content shape isn't canonical.**
Some prompts include explicit output examples ("answer 0.20"),
some include negative examples ("If the carveout does not exist,
answer null"), some are pure free-text. The valence-ontology
`SKILL.md` documents the "four beats" template (Locate, Recognize,
Disambiguate, Format), but ~40% of non-empty prompts don't follow
it visibly. The variance is bounded but inconsistent.

**Action:** Document the canonical prompt template (the SKILL.md
"four beats" form) in the methodology architecture. Don't enforce
via schema (TypeDB lacks a free-text-structure constraint). Future
prompts authored against the SKILL.md are conformant; existing
non-conformant prompts stay until iterated for other reasons.

**Compounding fix #3: empty-prompt questions can't follow the
canonical template by definition.** Connects to Fix #1 — same
121-question class; documenting the "empty-prompt design choice"
covers both.

## Dimension 3 — Value convention enforcement (Phase F's deferred
prompt-side work)

**Convention violation: systemic.**

Per the survey:
- 1 of 238 questions enforces percentage decimal convention
  (Phase F's chosen Convention 1).
- 0 of 238 enforce numeric percentage form.
- 6 of 238 enforce USD raw form (others mention dollar amounts
  generically, no explicit "raw vs millions" enforcement).

For the 11 percentage-answer-type questions specifically:

| Question | Prompt content | Enforces decimal? |
|---|---|---|
| rp_c5, rp_f3, rp_f5, rp_f6, rp_j5, rp_t21, rp_l11 | empty | n/a (skipped class) |
| rp_f13 | "Common values: 100%, 125%, 140%, 150%" | ✗ (numeric form implied) |
| rp_n2 | "'100% of EBITDA' or '1.5% of Total Assets'" | ✗ (numeric form in example) |
| rp_l20 | "answer 0.20. If no EBITDA percentage exists, answer null" | ✓ (decimal explicit) |
| rp_l22 | "answer 0.40. If no EBITDA percentage exists, answer null" | ✓ (decimal explicit) |

The dominant pattern in non-empty percentage prompts is mixed:
rp_l20/l22 enforce decimal explicitly; rp_f13/rp_n2 use numeric
form in examples without explicit guidance. Phase F's commit 4
documented Convention 1 as decimal but flagged the mixed state;
this audit confirms the divergence.

**Compounding fix #4: pick the convention direction (audit
decides per Phase H).**

Per the user's confirmation: "the audit decides. If decimal is
dominant, lock convention 1 as decimal; if numeric is dominant,
flip the convention."

**Decision: lock decimal.** Reasons:

- The two newest, most explicit prompts (rp_l20, rp_l22) enforce
  decimal. They were authored as ground-truth-anchored, post-Phase D2
  extraction work.
- The non-conforming prompts (rp_f13, rp_n2) lack explicit
  enforcement; they show numeric values in examples but don't say
  "return as numeric." The LLM may extract either form depending on
  the agreement text it reads.
- v3_data_normalization's "fraction → percentage" coercion was
  built to accept decimal inputs and convert to numeric for v3 GT
  YAML compatibility. Locking the v4 convention as decimal aligns
  with what extraction prompts ALREADY produce in the explicit cases.

**Compounding fix #5: update non-conforming percentage prompts.**

5 specific prompts to update (or empty prompts to author with the
canonical convention):
- rp_f13: "If an EBITDA minus Fixed Charges test exists, what
  multiple of Fixed Charges is subtracted? Common values are 100%
  (1.0), 125% (1.25), 140% (1.40), 150% (1.5). Return as a decimal
  fraction."
- rp_n2: similar — change "100% of EBITDA" examples to "(decimal:
  1.0)" form and explicit "Return as a decimal fraction."

This is bounded scope (2 specific prompt updates that compound
because they document the canonical pattern future extractions
follow).

**Compounding fix #6: deprecate v3_data_normalization.**

Phase F commit 5 marked the module DEPRECATING IN PHASE G with a
revisit trigger. Phase G didn't touch extraction; the trigger
remains. With Convention 1 now locked as decimal in Phase H:

- The `_SCALE_COERCION_ATTRS` list (which v3_data_normalization
  scales by 100x to convert fractions to percentages) describes
  a v3-vs-GT-YAML mismatch that's IRRELEVANT to v4. v4's
  synthesis_v4 reads decimal-convention attrs directly.
- The function exists as a Rule 5.2 concession ONLY for the v3
  ground-truth YAML pipeline. v4 doesn't need it.

**Decision:** mark `_normalize_v3_data` as a v3-only path; remove
the import from `extraction.py` IF the v3 pipeline genuinely
doesn't depend on it for v4 extractions. Phase H Commit 5 makes
the determination.

**Non-compounding findings (5 specific question_ids, deferred):**

- rp_l25 (Phase E null result for `product_line_2_10_c_iv_threshold`)
  — single deal, single question, prompt iteration would close it
  but doesn't affect convention systemics. Deferred per
  non-compounding rule.
- rp_c5, rp_f3, rp_f5, rp_f6, rp_j5, rp_t21 — empty-prompt
  percentage questions. Each requires authoring a prompt; the
  authoring is per-question work, doesn't compound. Deferred per
  Fix #1 documentation.

## Dimension 4 — Question→target traceability

**Aligned.** 257 attribute annotations across 232 of 238 questions.
The 6 questions without any annotation/target/category linkage are:

- 4 entity_list questions (`rp_el_sweep_tiers`, `rp_el_pathways`,
  `rp_el_reallocations`, `rp_el_exceptions`) — by design.
  Entity_list questions store their target via `target_entity_type`
  and `target_relation_type` attributes on the question itself,
  not via separate annotation relations.
- 2 jc_t questions (`jc_t1_34`, `jc_t2_30`) — J.Crew-specific
  questions. Likely need annotation; mild gap but small in absolute
  terms.

**Compounding fix #7: document the entity_list question pattern**
in the methodology architecture. The traceability gap for jc_t
questions is non-compounding (2 specific questions, J.Crew-only);
document and defer.

## Dimension 5 — Versioning discipline

**No infrastructure.** No `version_id`, `last_modified`, or
`replaces_question` attributes on `ontology_question`. When a
prompt is iterated (e.g., via the upsert pattern), prior prompt
text is lost.

**Per locked scope:** "Don't add versioning attributes; surface
as audit finding; defer to post-pilot."

**Action:** documented finding only. No schema additions in Phase H.
Post-pilot scope.

## Dimension 6 — Universe handling

**Per question, not per prompt.** Universe slicing is
extraction-pipeline-side: `extract_covenant()` runs each question
against the full universe (~446K chars on Duck Creek). Phase E
commit 0's incremental CLI doesn't change this — even when filtered
to 1 question, the universe is still full.

**Per locked scope:** "Universe-slice infrastructure for cost
reduction is post-pilot."

**Action:** documented finding only. Post-pilot scope. Phase E's
commit 3 doc (`docs/v4_phase_e/q4_carveout_extraction_run.md`)
already noted this; Phase H confirms it as a systemic property
of current extraction architecture.

## Dimension 7 — Storage interface coherence

**Aligned.** Phase F commit 1 + commit 3 established storage
idempotency for `provision_has_answer` (via `_upsert_relation_by_role_players`)
and `basket_reallocates_to` (via the pre-delete pass in
`wire_reallocation_edges`). Phase F commit 1's audit also
acknowledged the remaining `store_*` paths in `graph_storage.py`
(`store_extraction`, `_store_entity_list`, `_store_single_entity`)
still INSERT directly but only run under full `extract_covenant`
flow which is preceded by `delete_deal()` — so they're not
triggered by incremental extraction.

Phase H confirms this division. The `extract_covenant` path
(triggered by API endpoint or full pipeline) goes through
delete-then-insert; the incremental CLI path uses upsert via
`store_scalar_answer`. Both are idempotent in their respective
contexts.

**Action:** none. The aligned division is documented in
`docs/v4_storage_patterns.md` (Phase F commit 1).

## Dimension 8 — Question category alignment

**Aligned mostly.** 232 of 238 questions linked to ontology_categories
via category_has_question. 6 unlinked (per Dimension 4 finding):
4 entity_list (by design) + 2 jc_t (mild gap).

The category vocabulary used by extraction questions matches the
synthesis-side vocabulary (`docs/v4_attribute_conventions.md`'s
discussion of category_id formats: single letter A-Z plus JC1/MFN1/
DI1+ codes). Phase D2 commit 3's `total_capacity` synthesis_guidance
uses category N which corresponds to extraction questions
rp_n* (n_2, n_6, etc.) — same category vocabulary, both layers.

**Compounding fix #8 (deferred per cap; documenting only):** the 2
jc_t unlinked questions could be linked to category K (J.Crew
Blocker) but the work is small + per-question. Document as known
gap; treat as Tier 2 fix below the ≤15 budget cap.

Wait, let me re-count: actual compounding fixes Commit 2 lists are
#1 through #7 (skip the originally-numbered #8 since I downgraded
it). That's 7 compounding fixes within the ≤15 budget cap.

## Items going to Commit 3 (authoring + traceability discipline)

1. Document the empty-prompt design choice (Fix #1) in
   methodology architecture.
2. Document the canonical prompt template referencing the SKILL.md
   "four beats" form (Fix #2/#3).
3. Build `phase_h_validate_extraction_questions.py` validation
   utility (per user's "conformance attrs only if utility queries
   them" — IF the utility queries new attrs, the schema attrs come
   here; otherwise documentation-only).

## Items going to Commit 4 (SSoT discipline)

7. Document the entity_list question pattern in methodology
   architecture (Fix #7). Codify that entity_list questions encode
   their target via question-level attrs, while scalar questions
   encode it via question_annotates_attribute relations. Both are
   SSoT-aligned via different mechanisms.

(No SSoT violations found in the storage interface — Dimension 7 is
already aligned.)

## Items going to Commit 5 (convention enforcement, extraction-side)

4. Lock Convention 1 as decimal form (Fix #4); update
   `docs/v4_attribute_conventions.md` to reflect the locked
   direction.
5. Update non-conforming percentage prompts (Fix #5):
   - rp_f13: add explicit "Return as decimal fraction (e.g., 1.40
     for 140%)" guidance
   - rp_n2: same pattern
6. Audit `app/services/v3_data_normalization.py` (Fix #6); decide:
   keep as Rule 5.2 v3-only concession with explicit annotation,
   or remove if v4 doesn't need it.

## Non-compounding items deferred (documented in known-gaps)

- rp_l25 prompt iteration for `product_line_2_10_c_iv_threshold`
  null result — Phase E single-deal finding.
- rp_c5, rp_f3, rp_f5, rp_f6, rp_j5, rp_t21 — empty-prompt
  percentage questions; per-question authoring deferred.
- 117 empty-prompt questions broadly — per-question authoring
  work; not compounding as a single fix.
- jc_t1_34, jc_t2_30 — 2 specific category-link gaps; per-question
  fix.

## Summary stats

- Audit dimensions: 8
- Compounding fixes total: 7 (within ≤15 cap)
- Non-compounding findings deferred: ~125 (mostly the empty-prompt
  class)
- Schema additions proposed: 0 (Commit 3 may add 1 if validation
  utility queries it)
- Re-extractions required: 0 in Commit 3 / 4; possibly 1-2 in Commit
  5 if percentage-prompt updates need verification ($0.43-1.84)
