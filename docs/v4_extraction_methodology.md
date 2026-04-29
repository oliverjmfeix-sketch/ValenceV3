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

## Entity_list question pattern (architectural rationale)

`entity_list` is one of 9 valid `answer_type` values, but it has a
distinct architectural shape from scalar question types. This
section documents the pattern and the SSoT division it preserves.

### Shape comparison

A SCALAR question (e.g., `rp_l24`):
- Stores its target via two relations:
  - `category_has_question` (links to ontology_category for routing)
  - `question_annotates_attribute` (with `target_entity_type` +
    `target_attribute_name` edge attrs — names which v3 entity attribute
    the answer populates)
- Answer is a single typed value (boolean, double, string, etc.)
  stored in `provision_has_answer` with the matching `answer_*` typed
  attribute.

An ENTITY_LIST question (e.g., `rp_el_reallocations`):
- Stores its target via two QUESTION-LEVEL attributes (not relations):
  - `target_entity_type` (e.g., `"basket_reallocates_to"`)
  - `target_relation_type` (e.g., `"basket_reallocates_to"` — same
    name when the entity is itself a relation, or different when the
    entity is connected to the provision via a different relation)
- Answer is a list of dicts; each dict creates a new instance of
  the target entity type with the listed attributes, plus a
  `target_relation_type` instance linking it to the provision.
- Does NOT use `category_has_question` (entity_list questions don't
  route via category — they target a specific entity type
  directly).

### Why the difference is principled

Both shapes preserve the SSoT division (graph stores domain content;
Python orchestrates).

- Scalars need category routing because the answer-targeting
  decision (which v3 attribute does this populate?) is one decision
  per question, encoded in a single relation. Category provides
  semantic grouping for synthesis-side work (per-category
  synthesis_guidance).
- Entity_list answers create MANY entities per call. The targeting
  decision (which entity type? which relation?) is a property of
  the question itself, not of a specific answer. Encoding at the
  question level lets the extraction pipeline create the right
  entities without requiring per-answer routing logic in Python.
  The 4 entity_list questions (rp_el_sweep_tiers, rp_el_pathways,
  rp_el_reallocations, rp_el_exceptions) each target a distinct
  entity type, and their target_entity_type / target_relation_type
  attrs make this explicit graph-data, not Python branching.

### What stays in Python (orchestration)

- Looping over answers and calling the storage helper per item
- Resolving role players (e.g., resolving basket_id strings to
  actual entities for `basket_reallocates_to` relations)
- Filtering invalid items (e.g., skipping reallocations whose
  source/target basket can't be resolved)
- Idempotent storage discipline (Phase F's upsert helpers)

### What's in graph data (domain content)

- The question text, prompt, and target shape (per-question attrs)
- The category-question linkage (for scalar; absent for entity_list)
- The question's annotation linkage (for scalar; absent for
  entity_list)
- The target entity type + relation type (for entity_list;
  encoded as question attrs)

### Validation enforcement

The Phase H commit 3 validation utility
(`phase_h_validate_extraction_questions.py`) treats the two shapes
distinctly:

- For SCALAR types: requires `category_has_question` linkage AND
  (`question_annotates_attribute` OR `question_targets_field` OR
  `question_targets_concept`).
- For ENTITY_LIST: requires `target_entity_type` AND
  `target_relation_type` attrs on the question itself; does NOT
  require category linkage.

Conformance verified at 99.2% on valence_v4 (only the 2 jc_t
questions miss category linkage; both are scalar boolean type
where the linkage absence is a real gap, not a design choice).

## SSoT division summary

| Concern | Lives in | Authority |
|---|---|---|
| Question text + prompt + target shape | `ontology_question` graph entity | Graph |
| Category routing | `category_has_question` relation | Graph |
| Scalar attribute-target binding | `question_annotates_attribute` relation | Graph |
| Entity_list target-type binding | `target_entity_type` + `target_relation_type` attrs on question | Graph |
| Per-question prompt iteration | upsert via Phase D2 pattern | Graph (data); Python (mechanics) |
| Storage idempotency | `graph_storage._upsert_*` helpers | Python (mechanism); Phase F discipline doc (specification) |
| Universe slicing | `extract_covenant` runtime (currently full) | Python (current); future: per-question or per-category attribute |
| Convention enforcement | `extraction_prompt` content + `docs/v4_attribute_conventions.md` | Graph (per-question); Doc (canonical) |

No SSoT violations found in Phase H's audit. The division is
principled where it stands; future architecture changes (versioning
attrs, universe-slice attrs, conformance-attestation attrs) would
add to this table but are deferred to post-pilot.
