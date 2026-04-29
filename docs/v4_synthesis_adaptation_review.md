# v3-to-v4 synthesis adaptation review (Phase G commit 2)

> Audit whether the two-stage filter+synthesize pattern (inherited
> from v3's `/ask-graph`) fits v4's deontic data shape. Each pattern
> element categorized as: fits cleanly / fits with prompt-iteration
> distance / structural mismatch / SSoT violation. Recommendations
> route to Commit 3 (Phase G in-scope) or Phase H (deferred).
>
> Builds on Commit 1's authority audit (`docs/v4_synthesis_architecture.md`).

## Pattern element 1 — Stage 1 classification (PRIMARY/SUPPLEMENTARY/SKIP)

### v3 origin

In v3, Stage 1 was given a list of v3 provisions (one per covenant)
each carrying a flat attribute bag. The classifier asked "is this
provision relevant to the question?" — provision-grained relevance.

### v4 reality

v4's input is a list of NORMS (23 on Duck Creek). Each norm is
deontic-typed (`modality: permission|prohibition|obligation`),
scoped (subject/action/object/instrument edges), conditioned
(condition trees), and possibly defeated. The classifier still asks
"is this norm relevant?" but norm-level relevance is a different
question than provision-level relevance:

- A `prohibition` norm (J.Crew blocker) is "relevant" to a
  dividend-capacity question in a different way than a `permission`
  norm (general_rp_basket_permission). The blocker doesn't add
  capacity; it constrains designation. The classifier's PRIMARY
  bucket conflates both.
- A `permission` norm with `action_scope: 'specific'` (e.g.,
  management_equity_basket_permission for officer/director
  repurchases only) is "relevant" but in a tangential way (separate
  capacity pool, restricted purpose).
- A `permission` norm with `action_scope: 'reallocable'`
  (general_rp_basket_permission, general_rdp_basket_permission) is
  "relevant" as additive capacity for the aggregate question.

### Categorization

**Fits with prompt-iteration distance.** The current
`_STAGE1_SYSTEM_TEMPLATE` (Phase D2 commit 3) does refine PRIMARY
to handle deontic semantics: it explicitly calls out reallocable
basket-permission norms for capacity-aggregation questions. Phase
D2 verified this works (Stage 1 picks RDP basket as PRIMARY). The
prompt evolved as needed.

What's still iteration-distance:
- The PRIMARY/SUPPLEMENTARY/SKIP triplet is binary-flavored. v4
  could benefit from a finer classification: PRIMARY-CAPACITY,
  PRIMARY-CONDITIONAL, PRIMARY-DEFEATER, SUPPLEMENTARY-EVIDENCE,
  SKIP. But this is incremental refinement, not structural.

### Recommendation

No action in Commit 3. Stage 1 classification fits v4 with the
prompt iterations already applied. Future refinement is
post-pilot work.

## Pattern element 2 — Stage 2 reasoning + answer

### v3 origin

Stage 2 in v3 received Stage 1's filtered provisions plus the
synthesis_guidance for matched categories. The LLM reasoned over
the provision attribute bag and produced an answer. Authority
hierarchy: synthesis_guidance > generic prompt structure.

### v4 reality

Stage 2 in v4 receives:
- Filtered norms (Stage 1 PRIMARY + SUPPLEMENTARY)
- Defeaters
- proceeds_flows
- provision_level_entities (Phase D2 commit 4)
- synthesis_guidance (per matched category)

The LLM is asked to reason and produce a JSON answer with citations.

**Commit 1's diagnostic surfaced the structural issue:** Stage 2
attention to the FILTERED LIST inherits the fetch order's biases.
v3 didn't have this problem because v3 received a flat attribute bag
where order was meaningless. v4 receives an ORDERED LIST of norms
where order is a meaningful but architecturally-undeclared signal
to the LLM.

V4 probe (Q5, RDP norms reordered to front) confirmed: same
guidance, same data, position change → behavior change. The
synthesis_guidance authority over Stage 2 is graded — strong
enough to shape framing and answer structure, weak enough that
LLM attention to early-payload items overrides for citation
behavior.

### Categorization

**Structural mismatch + iteration-distance hybrid.**

- Structural: the v3 pattern's authority hierarchy (`guidance >
  generic prompt > LLM defaults`) doesn't hold in v4 for citation
  behavior because v4's ordered-list payload activates an LLM
  attention bias that v3's flat-bag payload didn't. The
  architecture should either:
  (a) Sort the payload by relevance before Stage 2 sees it (small
      fix, addresses Q5 mechanism c).
  (b) Add a fourth signal layer to the Stage 2 prompt that names
      the "must-cite" norms explicitly (larger change, applies
      across questions).
- Iteration-distance: the synthesis_guidance content for category N
  could be made more directive ("you MUST cite every reallocable
  fungible basket norm") to push back against the LLM's attention
  default. Empirically this hasn't been tested.

### Recommendation

- **Commit 3 in-scope:** apply (a) — sort `context.norms` so
  reallocable basket-permission norms classified as PRIMARY by
  Stage 1 lead the list, before Stage 2 sees them. This is the
  bounded fix matching the Q5 (c) mechanism finding.
- **Phase H scope:** generalize (a) into a relevance-scoring
  scheme that applies across question categories.
- **Out of scope:** (b) — adding a "must-cite" layer is larger
  prompt restructuring; defer until (a)'s effect is measured.

## Pattern element 3 — The bridge to v3 entities (extracted_from)

### v3 origin

n/a (v3 didn't have v4 norms).

### v4 reality

Each v4 norm carries `extracted_from.v3_attrs` — a dict of the v3
entity attributes the norm was projected from. Phase D1 found 18/18
synthesis_guidance entries port verbatim from v3 to v4 because
guidance text references v3 attributes that are reachable via this
bridge.

This is pragmatically useful. But architecturally:

- Stage 2 sees BOTH the v4 norm scalars (`cap_usd`, `cap_grower_pct`,
  `action_scope`, `capacity_composition`, `modality`) AND the v3
  attrs (`capacity_category`, basket-specific booleans, etc.).
- Stage 2's authority hierarchy when v4 and v3 attrs encode the
  same concept differently is unclear. The Phase D2 README
  documented one such case: Q5 Stage 2 filters by v3
  `capacity_category` ("general_purpose"), excluding v4-reallocable
  RDP. The v3 vocab took precedence over v4's structurally-correct
  marker.

### Categorization

**Iteration-distance.** Phase D2 commit 3 already addressed this
specifically by replacing category N's synthesis_guidance with
v4-aware content (referencing `action_scope: reallocable`,
`capacity_composition: fungible/additive`) instead of v3-era
`capacity_category`. The new guidance was load-bearing per V2
probe (Stage 1 PRIMARY count dropped 10 → 5 when removed).

What's still iteration-distance:
- Other categories' synthesis_guidance still uses v3 vocab. Phase
  D1's bulk migration ported 18/18 verbatim; only category N has
  been v4-rewritten.
- Phase D2's pattern (replace v3 vocab with v4 vocab in
  synthesis_guidance) generalizes; applying it to all 18 categories
  is ongoing work, not a phase-bounded commit.

### Recommendation

- **Out of scope for Commit 3:** wholesale rewrite of all 18
  categories' guidance. That's iterative work.
- **Phase H scope:** systematic rewrite if the audit reveals other
  categories' guidance is actively misleading Stage 2.
- **For Commit 3:** document the pattern (Phase D2 commit 3 as
  exemplar) in the synthesis content division doc. Subsequent
  category-by-category rewrites follow the pattern when surfaced
  by future eval residuals.

## Pattern element 4 — Authority hierarchy under conflict

### What does the architecture say?

Authority hierarchy (declared by the codebase + documentation):

1. Hard constraints in Python prompts (system prompt JSON output
   schema, classification buckets, etc.) — invariant.
2. `synthesis_guidance` per matched category — domain authority.
3. `stage1_picker_guidance` per matched category — picker bias.
4. Norm attribute values — what the data says.
5. LLM defaults — implicit; unconstrained beyond the above.

### What does the architecture do under conflict?

Probe-tested examples:

| Conflict | Architecture says | Architecture does |
|---|---|---|
| Stage 2 should cite RDP basket (synthesis_guidance for N), but RDP is at non-prominent payload position | Guidance authority > LLM defaults | LLM attention defaults win for citation; guidance wins for framing |
| Stage 2 should enumerate carveouts (synthesis_guidance for L), but carveouts are deeply nested attrs | Guidance authority > LLM defaults | LLM defaults win for enumeration; guidance has weak measurable effect |
| Stage 1 should mark RDP as PRIMARY (stage1_picker_guidance for N) | Guidance authority > LLM defaults | Guidance wins (Phase D2 commit 3 verified) |

Stage 1's authority hierarchy is honored. Stage 2's authority
hierarchy is honored for framing but not for citation/enumeration.

### Categorization

**Structural mismatch.** The architecture documents an authority
hierarchy that doesn't hold uniformly in practice. Stage 2's LLM
defaults (attention to early-payload items) silently override
guidance authority for some classes of behavior. This is the
load-bearing mismatch for Phase G's findings.

The architecture should either:
- Make the authority hierarchy enforceable (Commit 3's payload
  sort: align payload position with declared authority so they
  don't conflict), OR
- Document that the hierarchy is graded (guidance dominates for
  framing/structure; payload position dominates for
  citation/attention) and design subsequent work around that
  reality.

The first option is cleaner architecturally. The second is more
honest about LLM behavior.

### Recommendation

- **Commit 3 in-scope:** the payload-sort fix (per pattern
  element 2). Choose option (a): make authority enforceable by
  aligning payload position with Stage 1's classification authority.
- **Commit 6 outcome doc:** document the graded-authority finding
  for future reference. Even after Commit 3's fix, future
  architectures should expect that ordered-payload LLM attention
  is a real signal that needs to be designed FOR, not assumed
  away.

## Summary table

| Element | Categorization | Action |
|---|---|---|
| Stage 1 classification | Iteration-distance (already applied) | None |
| Stage 2 reasoning + answer | Structural mismatch + iteration-distance | Commit 3: payload sort |
| v3 entity bridge (extracted_from) | Iteration-distance (Phase D2 exemplar) | None for Commit 3; document pattern |
| Authority hierarchy under conflict | Structural mismatch | Commit 3: payload sort makes it enforceable |

## Items for Commit 3

1. **Payload sort in `fetch_norm_context`** — push norms with
   `action_scope: 'reallocable'` to the front of `context.norms`.
   The Stage 1 classification (PRIMARY) signal could also be used
   if Stage 1 runs before Stage 2 sees the sorted list — but
   currently Stage 1's output is a set of IDs, not a position-aware
   ordering. The simpler fix: sort by `action_scope` value at fetch
   time (no Stage 1 dependency).
2. **Optional V5 probe for Q4** — if cost budget allows, test
   whether elevating `asset_sale_sweep` carveout flags out of the
   nested attribute position changes Q4 enumeration. If yes,
   another payload-restructuring fix lands. If no, Q4 phenomenon
   is mechanism (a) and stays documented.

## Items for Phase H (out of Phase G scope)

1. **Generalized relevance scoring.** A scheme where the fetch
   path computes a relevance score per norm (using attributes,
   conditions, defeaters, and the question text) and orders the
   payload by it. Larger architectural change.
2. **Stage 2 must-cite layer.** Adding a structured "the LLM must
   cite all of these norms" directive to Stage 2's payload.
   Possibly as a fourth content layer alongside primary_norms /
   supplementary_norms / proceeds_flows / provision_level_entities.
3. **v3-vocab guidance rewrite for remaining categories.**
   Systematic rewrite using the Phase D2 commit 3 pattern. Scope
   per-category.
4. **Stage 1 finer classification.** PRIMARY-CAPACITY,
   PRIMARY-CONDITIONAL, PRIMARY-DEFEATER, etc. Not blocking; future
   refinement.
