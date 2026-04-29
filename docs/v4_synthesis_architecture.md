# Synthesis architecture audit (Phase G commit 1)

> Comprehensive audit of synthesis_v4's authority hierarchy plus
> empirical disambiguation of the Q4/Q5 Stage 2 override phenomenon
> via three-phase controlled-variation diagnostic. Findings shape
> Commit 2 (adaptation review), Commit 3 (SSoT discipline scope),
> and Phase H scope.

## Diagnostic methodology (re-runnable)

Three-phase methodology disambiguating between three possible
mechanisms for the Stage 2 override:

- **(a) LLM stylistic choice** — Stage 2 LLM, given correct data and
  correct guidance, makes presentation/inclusion decisions that
  diverge from gold answer expectations. Not directly testable; an
  inferred residual when neither (b) nor (c) explains observed
  behavior.
- **(b) Prompt-iteration distance** — `synthesis_guidance` content
  is being ignored or under-weighted by Stage 2. Tested by emptying
  the relevant category's guidance and observing whether behavior
  stays the same (= guidance was already being ignored) or changes
  substantially (= guidance was load-bearing).
- **(c) Data-presentation issue** — payload depth, position, or
  encoding suppresses signal that the LLM would otherwise weight.
  Tested by reordering the payload (push target items to the front)
  and observing whether attention shifts.

**Probe runs** (script: `app/scripts/phase_g_synthesis_diagnostic.py`):

- Phase 1 baselines (2 runs): Q4 + Q5 baseline, full configuration.
- Phase 2 controlled variations (4 runs):
  - V1: Q4 with category L `synthesis_guidance` filtered out
    (effective empty for category L only) — tests mechanism (b)
    for Q4.
  - V2: Q5 with category N `synthesis_guidance` filtered out — tests
    mechanism (b) for Q5.
  - V3: Q4 with `provision_level_entities.by_type` reordered so
    `sweep_tier` and `asset_sale_sweep` lead — tests mechanism (c)
    for Q4 at the entity-type position level.
  - V4: Q5 with `context.norms` reordered so RDP-related norms lead
    — tests mechanism (c) for Q5 at the norm-position level.
- Phase 3: synthesize findings.

**Total cost:** $0.85 (Phase 1: $0.28; Phase 2: $0.57). Slightly
over the planned $0.78 estimate; within the locked $0.80 budget
ceiling for principled disambiguation.

**Probe results location:**
`docs/v4_synthesis_diagnostic_runs/20260429T144057Z/`. Per-probe JSON
artifacts plus `SUMMARY.md`.

## Per-question findings

### Q5 (total dividend capacity) — RDP exclusion: mechanism (c)

**Phenomenon:** Stage 2 systematically excludes `general_rdp_basket_permission`
from the dividend capacity sum despite Phase D2 commit 3's picker
guidance correctly classifying it as PRIMARY at Stage 1.

**Evidence:**

| Run | Stage 1 | Stage 2 cites | RDP basket cited? | Answer summary |
|---|---|---|---|---|
| Baseline | 10P / 9S / 9K | 10 cites | ✗ No | "$260M floor" |
| V2 (no N guidance) | 5P / 15S / 8K | 9 cites | ✗ No | "Multiple pools by type" |
| **V4 (RDP norms reordered to front)** | 5P / 15S / 8K | **10 cites** | **✓ Yes (`general_rdp_basket_permission`)** | "Reallocable / General-Purpose Pools (summed)..." |

V2 dropped Stage 1 PRIMARY substantially (10P → 5P) confirming N
synthesis_guidance was load-bearing for picker behavior, but RDP still
absent from cites — so removing guidance doesn't explain the
exclusion either way.

V4 reordering produced the only behavior change that surfaced the
RDP basket. Same guidance, same data, only position changed in
`context.norms`. Stage 2 attention is sensitive to payload position.

**Mechanism conclusion: (c) data-presentation issue.** Payload
position determines whether Stage 2 weights a norm enough to cite
it. The RDP basket has the right attributes (`action_scope:
'reallocable'`, `cap_usd: 130000000`), correct picker guidance flags
it as PRIMARY at Stage 1, but Stage 2 doesn't see it as
load-bearing in its default position deeper in the norm list.

**This is a real architectural finding.** Synthesis_v4's behavior
depends on payload ordering, not just content. That's not a
property the architecture should have — it makes synthesis fragile
to graph-state changes (reordering a fetch query's `select` clause
could move citations).

### Q4 (asset sale proceeds) — carveout non-enumeration: ambiguous between (a) and (c)

**Phenomenon:** Stage 2 mentions "leverage-based exemptions" generically
but doesn't enumerate Section 2.10(c)(iv) product-line exemption
or Section 6.05(z) unlimited basket carveout by name, despite Phase
E commit 2 populating the relevant flags on `asset_sale_sweep`
(`permits_product_line_exemption_2_10_c_iv = True`,
`permits_section_6_05_z_unlimited = True`,
`section_6_05_z_threshold = 6.0`).

**Evidence:**

| Run | Stage 1 | Stage 2 cites | Carveouts named? |
|---|---|---|---|
| Baseline | 2P / 11S / 15K | 4 cites | ✗ "Section 2.10(c)" generic |
| V1 (no L guidance) | 2P / 11S / 15K | 5 cites (+unsub_equity) | ✗ Generic |
| V3 (sweep_tier + asset_sale_sweep entity types prioritized) | 2P / 11S / 15K | 5 cites (+unsub_equity) | ✗ Generic |

Both variations moved cite count 4 → 5 but neither produced explicit
2.10(c)(iv) / 6.05(z) enumeration.

**Why V3 didn't surface the carveouts:** the variation reordered
entity TYPES within `provision_level_entities.by_type` (sweep_tier
+ asset_sale_sweep moved to front). But the carveout FLAGS are
attributes WITHIN the `asset_sale_sweep` entity, not separate
top-level entity types. Reordering at the entity-type level
doesn't address whether attributes within that entity are buried.

**Mechanism conclusion: weak evidence for (a) or (c)-attribute-level.**
- (b) ruled out: V1 emptying L guidance moved behavior, so guidance
  has effect (just not the carveout-enumeration effect). The
  guidance authored in Phase E commit 4 is load-bearing for
  some changes but not strong enough to force enumeration.
- (c)-entity-type-position ruled out: V3 reordering didn't help.
- (c)-attribute-position untested: would require reordering the
  attributes WITHIN `asset_sale_sweep`, which isn't natively
  supported by the fetch path (entities serialize via
  `_all_attrs_of` which returns whatever order TypeDB yields).
- (a) LLM stylistic choice: a plausible residual. Stage 2 may be
  consolidating the sweep tier mechanism (which IS one of the
  named cites) into the answer at the expense of enumerating
  parallel mechanisms.

**For Commit 3 / Phase H:** untestable in current architecture
without a fifth probe that elevates carveout flags out of the
deeply-nested asset_sale_sweep payload position. That probe would
be a small Commit 3 task IF Phase G accepts it.

### Synthesis: per-question mechanism categorization

| Question | Phenomenon | Mechanism | Action target |
|---|---|---|---|
| Q5 | RDP exclusion from sum | **(c) payload position** | Phase G Commit 3 if architectural fix is bounded; Phase H if broader |
| Q4 | Carveout non-enumeration | **(a) or (c)-attribute-level** | Phase G Commit 3 untestable variation worth running; otherwise document and defer |

## Authority audit (per-component)

### `topic_router.route()` (`app/services/topic_router.py:325`)

**Declared behavior:** Match question against ontology_category
keyword sets; return matched categories ordered by match strength
+ aggregated metadata (covenant_type, question_ids, all_target_fields).

**Observed behavior:** Aligned. For Q5 returns `[N, M, A, F, G, I, T]`
(7 RP categories); for Q4 returns `[L, F, B, P]` plus DI subcategories.
Routing produces multiple matched categories; downstream callers
aggregate guidance from all of them.

**Authority over Stage 1/2:** Indirect. Provides matched categories
list. Stage 1 reads `stage1_picker_guidance` from matched categories
via `TopicRouter.get_stage1_picker_guidance()`. Stage 2 reads
`synthesis_guidance` from matched categories via
`TopicRouter.get_synthesis_guidance()`. The aggregation is "concat
all matched categories' guidance".

**Authority finding: aligned.** TopicRouter is mechanical (keyword
match → category list); domain authority lives in graph entities
(per-category attributes).

### `synthesize_one_question()` (`app/services/synthesis_v4.py:579`)

**Declared behavior:** Orchestrate route → fetch → Stage 1 → Stage 2.

**Observed behavior:** Aligned. Route runs first; matched categories
feed both `get_synthesis_guidance` and `get_stage1_picker_guidance`;
fetch runs against valence_v4; Stage 1 produces classifications;
Stage 2 receives Stage 1 + full norm context + provision-level
entities and produces JSON answer.

**Authority finding: aligned.** Pure orchestration. No domain
content embedded.

### `run_stage1()` (`app/services/synthesis_v4.py:258`)

**Declared behavior:** Strict-JSON classification of each norm and
defeater into PRIMARY / SUPPLEMENTARY / SKIP using:

- `_STAGE1_SYSTEM_TEMPLATE` (system prompt) — generic instructions
  + bias toward inclusion + classification rules.
- `picker_guidance` (per-category, from TopicRouter) — category-
  specific PRIMARY-bias overrides; injected into the
  `{picker_guidance_block}` placeholder.

**Observed behavior:** Aligned with declarations. Phase D2 commit 3
verified the picker guidance for category N successfully flips
`general_rdp_basket_permission` from SUPPLEMENTARY/SKIP to PRIMARY.
V4 baselines today confirm Stage 1 picks RDP basket as PRIMARY when
N is among matched categories.

**Authority finding: aligned for picker selection.** SSoT-compliant:
the picker bias content lives in graph data
(`stage1_picker_guidance` attribute), not in Python; system prompt
in Python is structural (defines PRIMARY/SUPPLEMENTARY/SKIP buckets,
JSON output format, fallback behavior).

### `run_stage2()` (`app/services/synthesis_v4.py:434`)

**Declared behavior:** Synthesize JSON answer from Stage 1's PRIMARY
+ SUPPLEMENTARY norms plus full context (proceeds_flows,
provision_level_entities). Reads:

- `_STAGE2_SYSTEM_TEMPLATE` (system prompt) — generic synthesis
  instructions + JSON output schema + DATA FORMAT documentation
  including the `provision_level_entities` block (Phase D2 commit 4).
- `category_guidance` (per-category synthesis_guidance from
  TopicRouter) — category-specific reasoning instructions.

**Observed behavior:** Diverges from declarations on Q4 and Q5.

For Q5: synthesis_guidance for category N (Phase D2 commit 3,
v4-aware version) explicitly instructs:
> "Every norm with action_scope: 'reallocable' contributes to the
> aggregate dividend capacity, regardless of which covenant it
> nominally serves. Sum these explicitly."

Stage 2 is given guidance content + RDP basket norm in the payload
(at PRIMARY position from Stage 1) BUT does not cite it. V4 probe
shows reordering the norm to a more prominent payload position
flips behavior. **The guidance is being followed at the
"reasoning" level but not at the "citation" level under default
payload ordering.**

For Q4: synthesis_guidance for category L (Phase E commit 4)
instructs explicit enumeration of 2.10(c)(iv) and 6.05(z) carveouts.
Stage 2 doesn't enumerate. V1 confirms the guidance has measurable
effect (cite count moves) but not enough to force enumeration.

**Authority finding: SSoT division correct, BUT effective authority
attenuates with payload position.** synthesis_guidance content
authority over Stage 2 is graded — strongly affects framing and
answer structure (V1, V2 changed both materially), but doesn't
override LLM attention defaults that prioritize early-payload items
for citation. Cannot fix this with content alone; would need either
(a) prompt-iteration to make guidance stronger relative to payload
attention, or (b) fetch-architecture changes to surface
load-bearing items earlier in the payload.

**SSoT-compliance verdict: ambiguous.** No false content placement
(guidance is in the right layer); no false architectural placement
(orchestration is in Python, content is in graph). But the
"authority hierarchy" the architecture implies (graph-stored
guidance > LLM defaults) is empirically false for citation
behavior.

### `fetch_norm_context()` and `fetch_provision_entities()` (`synthesis_v4_fetch.py:578` and `:470`)

**Declared behavior:** Fetch deal-scoped norms, defeaters, scope
edges, conditions, proceeds_flows, and (Phase D2 commit 4)
provision-level entities.

**Observed behavior:** Aligned for completeness. The data Stage 2
needs is in the payload. Position within the payload is the
emergent issue (per V4 finding above).

**Authority finding: aligned for data-completeness; flagged for
position-blindness.** The fetch contract is "return everything
relevant to the deal." It doesn't claim to order by relevance —
TypeDB query order is arbitrary unless explicitly sorted, and the
fetch helpers don't sort. Synthesis_v4's behavior depends on this
order.

## Categorized findings

| Finding | Category | Action target |
|---|---|---|
| TopicRouter authority is aligned | Aligned | None |
| `synthesize_one_question` orchestration is aligned | Aligned | None |
| `run_stage1` SSoT-compliant picker authority | Aligned | None |
| `run_stage2` synthesis_guidance authority attenuates with payload position (Q5 RDP exclusion) | **Iteration-distance + Structural** | Commit 3 conditional / Phase H |
| Q4 carveout non-enumeration: untested (a) or attribute-level (c) | **Iteration-distance** | Commit 3 if low-cost probe is justified |
| Fetch helpers don't sort by relevance | **Structural** | Commit 3 if low-cost fix; Phase H otherwise |

## Bridge to Commit 2 (adaptation review)

The Q5 finding (mechanism (c) data positioning) is the load-bearing
input to Commit 2's "v3-to-v4 adaptation review." Specifically:
the two-stage filter+synthesize pattern was inherited from v3's
`/ask-graph` flow. Stage 1's classification compresses ~28 norms
to 10-13 PRIMARY; Stage 2 then receives this filtered list. **But
the filtered list is presented in the same arbitrary order as the
fetch returned them** — so Stage 2's attention to the filtered list
inherits the fetch order's biases. v3's fetch returned a flat
attribute set (one provision, many attributes); v4's fetch returns
an ORDERED LIST of norms where order is meaningful for LLM
attention but not for the architecture.

This is the structural-mismatch finding Commit 2 should examine.
The two-stage pattern adapts to v4 BUT inherits a v3 assumption
(payload position is irrelevant) that v4 violates.

## Bridge to Commit 3 (SSoT discipline)

Commit 3 conditionality (per Phase G plan):

- **If audit surfaces SSoT violations** → discipline applies.
- **If audit surfaces mechanism (b) iteration distance** → prompt
  iteration in scope.
- **If audit surfaces mechanism (a) or (c)** → documentation-only or
  fetch-architecture change.

Audit conclusion: **no SSoT violations** (synthesis content is in
graph entities; orchestration is in Python). Mechanism (c) for Q5;
ambiguous for Q4.

**Commit 3 scope recommendation:**

- **Small architectural fix (Phase G in-scope):** add a
  `position-by-relevance` sorting pass to `fetch_norm_context()`
  that puts norms with `action_scope='reallocable'` AND
  Stage 1 PRIMARY classification at the front of `context.norms`
  before Stage 2 sees them. This is the bounded fix that closes
  the Q5 mechanism (c) finding.

- **Larger architectural change (Phase H):** generalize the
  position-by-relevance pass to a relevance-scoring scheme that
  other questions could use. Out of scope for Phase G.

- **Q4 carveout enumeration:** run a fifth probe (V5) that elevates
  the asset_sale_sweep carveout flags to a top-level position in
  `provision_level_entities`. If V5 surfaces the named carveouts,
  the bounded fix is similar (re-shape provision_level_entities
  payload). If not, Q4 phenomenon is mechanism (a) and stays
  documented as a known limit. Commit 3 includes V5 if it fits the
  per-commit cost budget.

## Out of scope

- Phase H scope (the larger relevance-scoring scheme). Commit 6
  documents this.
- Q4/Q5 specific outcomes (per Phase G hard scope: outcomes are
  evidence not target).
- MFN/DI eval set adaptation.
