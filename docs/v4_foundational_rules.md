# Valence v4 — Foundational Rules

These are the non-negotiable invariants for v4 development. Every piece of code, schema decision, prompt, and review must respect them. A violation of a foundational rule is a bug regardless of local correctness.

The rules are organized into seven categories. Each rule states its rationale and the specific failure mode it prevents.

---

## I. Single Source of Truth (SSoT)

### 1.1 TypeDB is the authoritative source for schema, questions, concepts, mappings, and deontic rules
Application code reads from TypeDB; it never maintains parallel copies. Any list, taxonomy, or rule that could change over the lifetime of the product lives in TypeDB.

*Prevents:* drift between code and data; migrations that silently break when one side updates.

### 1.2 No hardcoded lists in application code
No Python or TypeScript file contains a hardcoded list of covenant types, question ids, entity types, action classes, or concept options. These all come from TypeDB via query.

*Prevents:* adding a new covenant requires changing application code; forgetting to update a hardcoded list in one file.

### 1.3 Schema introspection drives prompt generation, storage, and API shape
Adding `owns new_attribute` to an entity is the only change needed to capture a new field. Pipeline adapts automatically.

*Prevents:* per-attribute application code; per-field boilerplate.

---

## II. Deontic discipline

### 2.1 Legal conclusions live in the graph, not in prompt text
If a rule determines what a correct answer is, it is encoded as a typed entity, relation, or function. If it shapes how an answer reads, it is guidance. There is no third category.

*Prevents:* synthesis-guidance creep; unverifiable legal reasoning; fragile prose prompts.

### 2.2 Norms are first-class entities
A permission, prohibition, obligation, power, or exception is modeled as a `norm` instance with explicit modality, subject, scope, condition, and source — not as an attribute of some other entity, not as an implicit feature of an entity's location in the covenant.

*Prevents:* having to reconstruct norms at query time from scattered attributes; ambiguity about what a covenant actually requires.

### 2.3 Structural completeness is enforced at storage
A norm that lacks required fields (modality, subject, scope, source) cannot be committed. Validation runs as a TypeDB function before insert.

*Prevents:* partial-extraction norms poisoning downstream queries; silent failures.

### 2.4 Conditions are composable predicate trees, not parallel booleans
A condition like "no EoD AND (ratio < 5.75 OR no-worse)" is stored as a tree; it is not flattened into several boolean attributes on the norm.

*Prevents:* non-queryable conditions; re-derivation of condition semantics at query time; guidance text re-assembling the logic.

### 2.5 Defeaters are explicit edges
An exception that defeats a prohibition is a `defeats` relation with typed defeater_type. The defeat structure is queryable, not implicit in entity subclassing.

*Prevents:* ambiguity about norm precedence; conflict-resolution logic hiding in Python.

---

## III. Provenance

### 3.1 Every extracted fact has source_text, source_page, and source_section
No exceptions. If a fact can't be cited to the agreement, it isn't stored.

*Prevents:* ungrounded claims; answers that users cannot verify.

### 3.2 Every derived norm traces to at least one extracted fact
The `norm_extracted_from` relation is mandatory for every norm.

*Prevents:* norms appearing without justification; untraceable deontic conclusions.

### 3.3 No fabricated citations
If extraction can't find a source, the field is null. The storage layer rejects fabricated placeholder citations.

*Prevents:* Claude inventing authority.

### 3.4 User-facing answers cite source_section, not internal norm_ids
The renderer includes citations from the norm's provenance, not the graph's internal keys.

*Prevents:* leaking internal schema into user output.

---

## IV. Prompt hygiene

### 4.1 No section references in extraction or reasoning prompts
Section numbers vary per agreement. Prompts reference defined terms (e.g., "First Lien Incremental Term Facility") which are stable, not lexical section locations (e.g., "Section 2.19") which are not.

*Prevents:* prompts that silently break on agreements with different section numbering; over-fitting to a specific template.

### 4.2 Closed taxonomies with explicit per-option definitions
Any multiselect or classification prompt presents the option set with a 1-2 sentence description per option. Bare enums without context degrade Claude's accuracy.

*Prevents:* abstract-classification errors; taxonomy drift.

### 4.3 Classification accuracy is measured empirically before integration
A deontic classification (capacity_composition, action_scope, condition_structure) is not used by downstream operations until its accuracy on the test deal is measured and above the defined threshold (95% for the pilot).

*Prevents:* architectures built on misclassified inputs; silent degradation.

### 4.4 Neither extraction prompts nor rendering prompts contain legal reasoning
Extraction prompts describe what to extract. Rendering prompts describe how to present a structured result. Neither re-derives legal conclusions at runtime.

*Prevents:* synthesis-guidance creep under different names.

---

## V. Architecture separation

### 5.1 Four distinct layers with typed interfaces
Extraction → projection → reasoning → rendering. Each layer consumes the previous layer's typed output. No layer reaches across boundaries.

*Prevents:* entangled logic; untestable end-to-end behavior; guidance strings that couple layers.

### 5.2 Deontic logic lives in TypeDB functions, not Python
Condition evaluation, capacity composition, defeater resolution, pathway traversal, norm applicability — all implemented as `fun` in TypeQL. Python is a thin caller.

*Prevents:* business logic scattered across the codebase; Python that can't be reused across operations.

### 5.3 Rendering is prose generation only
The renderer takes a structured result and writes natural language. It does not reason, compute, or decide legal conclusions. All conclusions are present in its input.

*Prevents:* legal reasoning hidden in renderer prompts; hallucinated substance.

### 5.4 Operations are a finite, composable set
Infinite user questions are handled by (a) composing the finite operations and (b) a Path B fallback for genuinely open-ended requests. New operations are added deliberately, not ad hoc per question.

*Prevents:* operation sprawl; per-question special cases.

---

## VI. TypeDB 3.x idioms

### 6.1 Functions replace rules
No `rule` keyword. All inference as `fun`.

### 6.2 Explicit type kinds
`entity X`, `relation X`, `attribute X` — never `sub entity`, `sub relation`, `sub attribute`. Subtyping uses `sub` only for child-of-parent entity relationships.

### 6.3 3.x query syntax
`select` not `get`; `$` not `?`; `fetch` for structured JSON output. Reference `typedb_3x_reference.md` for any syntax uncertainty.

### 6.4 Transactions directly on driver, no sessions
Schema transactions for type definitions and functions; write for data; read for queries.

### 6.5 Polymorphic queries via `isa!` + `label($var)`
Queries that traverse type hierarchies do so by reflecting on the type at query time, not by enumerating subtypes.

---

## VII. Pilot discipline

### 7.1 RP first
All v4 development targets RP until the Duck Creek acceptance test passes. MFN, DI, and the 11 other covenants are stubs that return a clear "v4 supports RP only" message.

*Prevents:* boiling the ocean; half-working architecture across 14 covenants.

### 7.2 Ground truth is the baseline
The Duck Creek RP norm-graph ground truth (`app/data/duck_creek_rp_ground_truth.yaml`) is the fixed target. Every extraction run diffs against it; regressions are caught before merge.

*Prevents:* quality drift; silent coverage loss.

### 7.3 Every substantive change has a regression test
New operation → test. New function → test. New mapping → test. CI runs the full suite on every commit.

*Prevents:* breaking what works while fixing what doesn't.

### 7.4 No merge to main without acceptance-test pass
The `v4-deontic` branch does not merge to main until all 6 Duck Creek gold-standard questions pass v4's acceptance test with substantive agreement against the gold answers.

*Prevents:* half-validated v4 becoming the production line.

---

## VIII. World state discipline

### 8.1 World state is per-query input, not stored state
Valence's graph stores agreement state — norms, conditions, capacity formulas, defeaters, and their relations. Valence does NOT store world state — the borrower's current financial reality (leverage ratios, EBITDA, accumulated usage, EoD status, transaction-specific facts, proposed-action details).

Operations-layer functions that need world state accept it as a per-query parameter supplied by the consumer. Every evaluation is a pure function of (graph: agreement structure) + (supplied: world state). Nothing persists across calls about the borrower's current reality.

Response shape for evaluated queries:
- Echo the `supplied_world_state` (the consumer's inputs)
- Include a `computation_trace` showing which predicates/functions were evaluated with which supplied values
- Return the result

This preserves a clean audit trail: the consumer can verify Valence used their inputs correctly and can't be surprised by Valence substituting values of its own.

*Rationale:* the agreement defines rules. The borrower's current financial state is the borrower's to know, update, and certify — not Valence's to maintain. Storing world state creates a staleness liability (numbers drift the moment the next quarter closes) and a correctness liability (whose authoritative copy?). Accepting world state as input preserves Valence's role as agreement-explainer and evaluator-against-supplied-inputs without claiming authoritative knowledge of the world.

*Prevents:* stale-data claims surfaced as conclusions; Valence becoming a system-of-record it was never designed to be; disagreement between Valence's snapshot and the borrower's books.

**Practical consequences:**

- No `world_state` entity persisted in `valence_v4` as ongoing graph state
- No seed file that hydrates current leverage, EBITDA, etc. into the graph
- `predicate_holds`, `condition_holds`, and capacity functions accept world state via their signature (as they already do — `$ws: event_instance`); consumers pass it
- `evaluate_*` and `compute_*` operations-layer functions take `supplied_world_state` as a required parameter
- `describe_*` operations-layer functions (pure structure) don't need world state at all
- `event_instance` as a schema type remains, but serves as the *shape* of the input parameter. Instances live in the graph only transiently during a query's evaluation, not as persistent state

**Accept vs store distinction.** Accepting world state as input is necessary and correct — `predicate_holds($pred, $ws)` cannot evaluate a ratio test without being told what the ratio is. Storing world state as persistent graph state is the liability. This rule removes the stored pattern wherever it appears; the accept-as-input pattern is the design target for the operations layer.

---

## How to apply these rules

**When writing code:** if you are about to put a legal rule in a string, check 2.1. If you are about to hardcode a list, check 1.2. If you are about to flatten a condition into booleans, check 2.4.

**When writing a prompt:** if it contains a section reference, check 4.1. If it lists options without descriptions, check 4.2. If it asks Claude to reason about legal conclusions, check 4.4.

**When reviewing a PR:** every rule above is a review checklist item. A PR passing review but violating a foundational rule is a review failure, not a rule exception.

**When in doubt:** err toward storing more in the graph and less in prompts. Err toward TypeDB functions and less Python. Err toward explicit typed structures and less implicit convention.

These rules are modifiable only by explicit decision with reasoning documented. They are not defaults you optimize against locally.
