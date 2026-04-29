# Storage patterns for Valence v4 (Phase F commit 1)

## Why this document exists

Phase E surfaced two storage-layer issues:

1. `store_scalar_answer` and adjacent paths in `app/services/graph_storage.py`
   insert without dedup. Re-running an extraction question creates duplicate
   `provision_has_answer` relations.
2. `basket_reallocates_to.capacity_effect` storage produced two values on a
   single relation (cardinality violation).

Phase F commit 1 makes storage idempotent. This document captures the
empirically-verified semantics of TypeDB 3.8's `put` keyword and the
patterns chosen for each storage case in `graph_storage.py`.

## Probe findings

Probe script: `app/scripts/phase_f_probe_put_semantics.py`. Re-runnable
against `valence_v4`; cleans up between runs.

### Probe 1 — `put` for entity with `@key`

Two `put` queries with the same `@key` value.

```typeql
put $p isa rp_provision, has provision_id "phase_f_probe_entity_put_1";
# ... and again
put $p isa rp_provision, has provision_id "phase_f_probe_entity_put_1";
```

**Result:** Idempotent. After two puts: 1 entity. The second `put` matched
the existing entity (because the `@key` constraint forces dedup).

**Recommendation:** Use `put` for entity insert-or-match when the entity
type has an `@key` attribute. Pattern: `put $x isa <type>, has <key_attr> "<value>"`.

### Probe 2 — `put` for attribute on existing entity

Sets attr to `true`, then `put`s with `false`:

```typeql
match $p isa rp_provision, has provision_id "...";
put $p has jcrew_pattern_detected true;
# then
match $p isa rp_provision, has provision_id "...";
put $p has jcrew_pattern_detected false;
```

**Result:** Second `put` FAILS with:

```
[CNT5] Constraint '@card(0..1)' has been violated: found 2 instances.
[DVL10] Instance ... of type 'rp_provision' has an attribute ownership
constraint violation for attribute ownership of type 'jcrew_pattern_detected'.
```

Final state: original value `true` preserved (transaction rolled back).

**Interpretation:** `put` for an attribute on an existing entity does NOT
update the value. It tries to ADD a value. If the existing value is the
same, it's a no-op (idempotent for "set to same value"). If the new value
differs, it violates cardinality and fails.

**Recommendation:** Do NOT use `put` for attribute value updates. Use
match-delete-insert (Probe 4 pattern).

### Probe 3 — `put` for relation with `@unique`/`@key` edge attributes

Tested putting a `provision_has_answer` relation with `answer_id` (which
is `@unique` — Phase F audit will confirm). The probe used probe-prefixed
answer_ids.

**Result:** FAILS with `[CNT9] @unique violated for answer_id`.

**Interpretation:** The TypeDB 3.8 relation `put` semantics for relations
with attribute uniqueness constraints behaves like `insert` for the
attribute — re-using the same `answer_id` violates `@unique` even when
the role players are the same.

**Implication for `provision_has_answer` storage:** The current
`store_scalar_answer` generates a fresh `answer_id` per call (UUID
prefix). This is what creates duplicates — each call gets a new id, so
the `@unique` constraint doesn't prevent duplication on the relation
shape (provision, question, answer).

**Recommendation:** For relations that should be unique by role-player
tuple (not by attribute), use match-delete-insert in a single
transaction. This is the same pattern as Probe 4.

### Probe 4 — match-delete-insert (attribute update baseline)

Single-transaction sequence:

```typeql
match $p isa rp_provision, has provision_id "...", has jcrew_pattern_detected $existing;
delete has $existing of $p;
match $p isa rp_provision, has provision_id "...";
insert $p has jcrew_pattern_detected false;
```

**Result:** ✓ Final value is `[False]`. Pattern works correctly.

**Recommendation:** Use match-delete-insert for any attribute value
update where the new value may differ from the old. Two query objects
in one transaction (TypeDB 3.x's INF4 pattern; per
[docs/typedb_patterns.md §10](typedb_patterns.md)).

## Chosen patterns per storage case

### Case A: Entity with `@key` (insert or match)

```typeql
put $x isa <entity_type>, has <key_attr> "<value>";
```

Idempotent. Use for `_ensure_provision_exists_unified` and similar
entity-existence-check paths.

### Case B: Attribute on existing entity (set to same value or update)

```typeql
match $x isa <entity_type>, has <key_attr> "<value>";
try { match $x isa <entity_type>, has <key_attr> "<value>", has <attr> $existing; delete has $existing of $x; };
match $x isa <entity_type>, has <key_attr> "<value>";
insert $x has <attr> <new_value>;
```

Three queries in one transaction:
1. `try` block deletes any existing value (no-op if absent).
2. Insert sets the new value.

The `try` wrapper ensures the delete doesn't fail when the attribute
isn't currently set. Pattern lives in
`app/scripts/phase_d2_upsert_category_guidance.py` already; generalize it.

### Case C: Relation that should be unique by role-player tuple

For relations like `provision_has_answer` where (provision, question)
should map to at most one relation:

```typeql
# Step 1: delete any existing relation matching the role-player tuple
match
    $rel isa <relation_type>, links (<role_a>: $a, <role_b>: $b);
    $a isa <type_a>, has <a_key> "<a_value>";
    $b isa <type_b>, has <b_key> "<b_value>";
delete $rel;
# Step 2: insert the new relation with fresh edge attributes
match
    $a isa <type_a>, has <a_key> "<a_value>";
    $b isa <type_b>, has <b_key> "<b_value>";
insert (<role_a>: $a, <role_b>: $b) isa <relation_type>, has <attr> <value>, ...;
```

Two queries in one transaction. The first deletes any existing relation
with the same role-player tuple (cascading deletes its edge attributes
including `@unique` ones, freeing them for re-use); the second inserts
fresh.

This is the pattern `store_scalar_answer` needs.

### Case D: Relation where multiple instances per role-player tuple are
intentional

Example: `basket_reallocates_to` between (general_rp_basket,
general_rdp_basket) — the rp_el_reallocations prompt explicitly models
each direction as a separate entity, so two relations per
unordered-pair are correct.

The discipline here is that each direction's storage path matches by
DIRECTION (full role-player tuple including direction), not by
unordered-pair:

```typeql
# Storing the (RP -> RDP) direction
match $rel isa basket_reallocates_to, links (source_basket: $rp, target_basket: $rdp);
delete $rel;
match $rp isa general_rp_basket, has basket_id "...";
$rdp isa general_rdp_basket, has basket_id "...";
insert (source_basket: $rp, target_basket: $rdp) isa basket_reallocates_to,
    has capacity_effect "fungible", ...;
```

The KEY is the direction-specific role assignment. The Phase E
diagnostic showed this case was buggy — storage matched without
distinguishing direction, then wrote `capacity_effect` twice on the
same merged relation. Case D's pattern fixes that by deleting first.

## TypeDB-version-bounded compromises

None at TypeDB 3.8.0. The match-delete-insert pattern is sufficient for
all idempotency needs.

If TypeDB adds a true `upsert` for attributes on existing entities in a
future version, Case B simplifies. Until then, the three-query pattern
stands.

## Application to `graph_storage.py`

Phase F commit 1 implementation:

1. Add `_upsert_entity(tx, entity_type, key_attr, key_value, attrs)`
   helper using Case A `put`.
2. Add `_upsert_attribute(tx, entity_type, key_attr, key_value, attr_name, value)`
   helper using Case B match-delete-insert.
3. Add `_upsert_relation(tx, relation_type, role_player_specs, attrs)`
   helper using Case C match-delete-insert. The
   `role_player_specs` is a list of `(role, type, key_attr, key_value)`
   tuples.
4. Convert `store_scalar_answer` to use `_upsert_relation` against
   `provision_has_answer` keyed by (provision, question) tuple.
5. Audit `store_extraction`, `_store_entity_list`, `_store_single_entity`,
   `store_concept_applicability`, and any direct insert paths in
   `extraction.py`. Convert to upsert helpers as appropriate.

## Forward-only data discipline

Per Phase F's locked scope: this commit does NOT clean up existing
duplicate relations created by past extractions. The Commit 2 audit
will surface any current duplicates as findings. If found, they get
categorized (fix in Commit 3 or defer to known-gaps), not silently
backfilled.

## Cleanup for probe entities

The probe script's `cleanup_probes` deletes probe entities. The orphan
`answer_id` attributes from probe runs (which are `@unique` and persist
even after the parent relation is deleted) need separate cleanup; the
script's pre-probe cleanup pass handles them on subsequent runs.
