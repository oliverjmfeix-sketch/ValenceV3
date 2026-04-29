# Extraction methodology architecture

> Canonical authoring path, prompt structure, and design conventions
> for `ontology_question` entities. Authored as Phase H commit 3
> deliverable; this doc is the SSoT for "what does a well-formed
> extraction question look like."
>
> Validation utility: `app/scripts/phase_h_validate_extraction_questions.py`.
> Survey script: `app/scripts/phase_h_extraction_survey.py`.

## Canonical question authoring path

Every extraction question is an `ontology_question` entity in the
graph (SSoT — questions are graph data, not Python). The canonical
authoring path:

1. Edit the appropriate seed `.tql` file under `app/data/`:
   - `seed_new_questions.tql` for scalar questions
   - `seed_entity_list_questions.tql` for entity_list questions
   - `seed_*_ontology_questions.tql` for covenant-specific seeds (MFN,
     DI, etc.)
2. Run validation: `python app/scripts/validate_ontology.py <file>`
   (existing tool) plus the broader Phase H validation:
   `python -m app.scripts.phase_h_validate_extraction_questions`.
3. Migrate to the database via the seed loader (full reseed) OR via
   one-shot Phase D2-pattern upsert scripts (incremental).

For per-question modifications post-seeding (prompt iteration,
attribute additions), follow the Phase D2 upsert pattern:
`phase_d2_update_synthesis_guidance.py` is the canonical template
for upserting attribute values on existing entities.

## Required attributes (validated)

Every `ontology_question` MUST have:

- `question_id @key` — stable identifier in canonical format per
  category (e.g., `rp_l24`, `duck_creek_q1`, `rp_el_reallocations`).
- `question_text` — one-sentence human-readable form.
- `answer_type` — one of `boolean`, `integer`, `double`, `percentage`,
  `currency`, `number`, `string`, `multiselect`, `entity`,
  `entity_list`.
- `covenant_type` — one of the canonical covenant codes (`RP`, `MFN`,
  `DI`, `LIENS`, `INV`, `AS`, `EOD`, `FC`, `PP`, `AMD`, `FUND`,
  `AFF`, `PF`, `CP` per `docs/cowork/skills/valence-ontology/SKILL.md`).
- `display_order` — integer for UI ordering within category.

## Optional but conventional attributes

- `extraction_prompt` — the LLM-facing prompt. **Empty is a
  documented design choice** (see "empty prompt convention" below);
  not having a prompt is acceptable for seed-only or
  pre-populated-via-concept-applicability questions.
- `description` — secondary human-readable text for UI / context.
- `is_required` — per-question flag; defaults to false.
- `default_value` — when answer is null, this value is used.
- `storage_value_type` — `boolean` / `double` / `integer` / `string`;
  determines which `answer_*` typed attribute on
  `provision_has_answer` stores the value.
- `target_entity_type`, `target_relation_type` — REQUIRED for
  entity_list questions only. Encode the target-entity surface for
  the question's answer.

## Linkage relations

- `category_has_question(category: $cat, question: $q)` — links the
  question to its `ontology_category`. REQUIRED for all questions
  EXCEPT entity_list questions (which encode their target via
  question-level attrs and don't require a category for routing).
- `question_annotates_attribute(question: $q)` with
  `target_entity_type` + `target_attribute_name` edge attributes —
  used by scalar questions whose answer populates an attribute on
  a v3 entity.
- `question_targets_field(question: $q)` with `target_field_name`
  edge attribute — used by scalar questions targeting a flat field
  (not directly an entity attribute).
- `question_targets_concept(question: $q)` with
  `target_concept_type` edge attribute — used by multiselect
  questions to identify which concept value the answer applies to.

## Empty-prompt convention

**117 of 238 questions on `valence_v4` have empty `extraction_prompt`.**
This is a documented design choice, not a bug. Empty-prompt
questions fall into two classes:

1. **Seed-only questions.** Questions authored as schema for
   future extraction but not currently exercised. Examples: the
   21 empty-prompt questions in the rp_t (RDP baskets) family —
   schema for RDP boolean coverage that future extractions may
   populate when RDP-specific extraction prompts are authored.
2. **Concept-applicability-routed questions.** Questions whose
   answer comes from a `concept_applicability` relation derived
   elsewhere, not from a direct LLM extraction call. These don't
   need a runtime prompt because the answer is determined by
   structural concept matching.

When AUTHORING a new question:

- If you intend the LLM to actively extract this question against
  a covenant universe, **the `extraction_prompt` MUST be non-empty**
  and follow the canonical four-beat template (below).
- If the question is seed-only or routed via concept_applicability,
  empty `extraction_prompt` is acceptable. Document the routing in
  the `description` field or in a comment in the seed file.

## Canonical prompt template — four beats

When `extraction_prompt` is non-empty, it should follow the four-beat
template documented in
`docs/cowork/skills/valence-ontology/SKILL.md`:

1. **Locate.** Where in the agreement to look. Name the covenant
   (never the section number — numbers vary across deals).
2. **Recognize.** Two or three specific drafting phrasings to
   spot.
3. **Disambiguate.** What would make the answer true vs. false, or
   what would push the extractor off-track.
4. **Format.** The exact output shape — for integers: units; for
   strings: verbatim vs. normalized; for percentages: decimal
   convention (Phase F Convention 1 — see "value conventions" below).

**Strong example (rp_l24):**

> "Does Section 2.10(c)(iv) (or analogous mandatory prepayment
> exemption) provide that proceeds from the sale of a product line,
> line of business, or substantially all of a product line are
> EXEMPT from the mandatory prepayment sweep, subject to a
> leverage ratio test (e.g., First Lien Leverage Ratio at or below
> a stated threshold) OR a pro forma no-worse test? Look in the
> mandatory prepayment carveouts for 'product line', 'line of
> business', or 'substantially all assets used in' language coupled
> with a ratio test. Answer true if such an exemption exists."

The four beats are visible: Locate (Section 2.10(c)(iv) /
mandatory prepayment carveouts), Recognize ('product line', 'line
of business', etc.), Disambiguate ("subject to a leverage ratio
test… OR a pro forma no-worse test"), Format (boolean: "Answer true
if such an exemption exists").

**Weak example (rp_n2):**

> "If the general RP basket uses a 'greater of' formula, what is
> the EBITDA or Total Assets percentage? For example, '100% of
> EBITDA' or '1.5% of Total Assets'."

The four beats are partially absent — Locate is implicit (general
RP basket) but specific section reference would help; Format
doesn't enforce the percentage convention (see Phase H commit 5
fix).

## Value conventions (per `docs/v4_attribute_conventions.md`)

Phase F documented six attribute conventions; Phase H commit 5
locks Convention 1 (percentage) as decimal form. Extraction
prompts for each convention class should explicitly enforce:

- **Percentage attributes:** "Return as a decimal fraction (e.g.,
  0.15 for 15%, 1.0 for 100%)." Per Convention 1 (decimal form
  locked Phase H commit 5).
- **Monetary attributes:** "Return as a USD raw integer (e.g.,
  130000000 for $130M), non-negative." Per Convention 2.
- **Boolean attributes:** Positive-framed name (`permits_*`,
  `requires_*`, `includes_*`); answer true when the named thing
  IS the case. Per Convention 3.
- **Identifier attributes:** Per-class format (e.g., `norm_id`
  follows `<deal_id>_<categorical_slug>`). Per Convention 4.
- **Source-text attributes:** Verbatim quote, ≤ 2000 chars
  (truncated by `_escape` at storage time). Per Convention 5.
- **Enum-string attributes:** Document the canonical value list in
  the prompt and in a schema comment. Per Convention 6.

## Versioning discipline (deferred to post-pilot)

Currently no `version_id` or `last_modified` attributes on
`ontology_question`. When prompt is iterated via the upsert pattern,
prior prompt text is overwritten without preservation.

For the pilot, this is acceptable — iteration is rare and the seed
files are version-controlled in git, so the canonical history lives
there. Post-pilot work may add `version_id` if questions iterate
frequently enough to justify in-graph tracking.

## Universe handling (deferred to post-pilot)

Currently every question runs against the full universe (~446K
chars for Duck Creek RP). Per-question or per-category universe
slicing is a known cost-reduction opportunity (Phase E commit 3
documented ~$0.43/question average; would drop ~10x with proper
slicing). Documented in `docs/v4_phase_e/q4_carveout_extraction_run.md`.

## Storage interface

Extraction writes go through Phase F's idempotent storage
discipline. Two paths:

- **Full extraction** (`extract_covenant` invoked via API): preceded
  by `delete_deal()` clearing prior state; insert paths are
  idempotent via clear-then-insert.
- **Incremental extraction** (Phase E commit 0 CLI): uses
  `_upsert_relation_by_role_players` for `provision_has_answer` and
  `wire_reallocation_edges` pre-delete pass for
  `basket_reallocates_to`. Re-running a question for the same
  (provision, question) tuple is a no-op state-wise (overwrites
  existing answer).

See `docs/v4_storage_patterns.md` for the patterns. Future
extraction-side writes that don't go through these helpers are
out-of-discipline and should be flagged.
