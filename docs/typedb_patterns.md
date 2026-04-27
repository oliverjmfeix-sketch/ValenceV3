# TypeDB 3.x patterns + known gotchas

Short-form reference for pilot-learned syntax pitfalls and their
workarounds. Consult this before writing new TypeQL to avoid
re-discovering the traps below.

Each entry names the symptom, minimal failing example, root cause, and
workaround. If a gotcha fires in production, it belongs here.

---

## 1. INF11 trap on `try { ... }` attribute reads

**Symptom.** A query wrapped in `try { $e has some_attr $v; }` returns
zero rows and logs:

```
[INF11] Type-inference was unable to find compatible types for the
pair of variables 'e' & 'v' across a 'has' constraint.
Types were:
- e: [<entity_type>]
- v: ...
```

`try` is supposed to make attribute access optional, but the failure is
at compile time, not runtime — the whole query aborts before execution.

**Failing example.**

```typeql
match
  $e isa! jcrew_blocker;
  try { $e has source_section $ss; };   # INF11: jcrew_blocker owns
                                        # section_reference, not source_section
select $e, $ss;
```

**Root cause.** TypeDB 3.x runs type inference at query-compile time.
`try` suppresses *runtime* attribute-missing errors, not compile-time
type-system violations. If the entity type doesn't declare the
attribute anywhere in its owns list (or via its parent hierarchy), the
entire query is rejected before any row is fetched.

**Workaround.** Query only attributes the type actually owns. If the
caller needs a uniform attribute name across entity types with
different owns declarations, normalise at the Python layer after
fetching, or split the query into per-type branches.

```typeql
match
  $e isa! jcrew_blocker;
  try { $e has section_reference $sref; };   # attribute jcrew_blocker owns
  try { $e has source_page $sp; };
  try { $e has source_text $st; };
select $e, $sref, $sp, $st;
```

**Prior occurrence.** Prompt 10 Fix 3 (commit `2be78a6`) added
`try { $e has source_section $ss; }` to the non-basket fetch. The query
silently returned zero entities for a week, suppressing the J.Crew
prohibition norm and all 5 defeaters. Caught by pre-Prompt-11 source
verification; remediated by commit `760d16e`. A6 harness invariant now
catches future regressions of this class (commit for the invariant
follows this doc).

---

## 2. Composite keys via ID concatenation

**Symptom.** `@key` and `@unique` annotations across multiple
attributes don't parse. Block-syntax composite keys are rejected:

```typeql
attribute state_predicate_id, value string;  # @key works per-attribute
# But you can't key on (label, threshold, operator, reference) together.
```

**Root cause.** TypeDB 3.x doesn't support composite (multi-attribute)
keys or uniqueness constraints. See `docs/v4_deontic_architecture.md`
§4.5.1.1 for the full ruling.

**Workaround.** Construct a composite identity attribute by
pipe-delimiting the constituents. `app/services/predicate_id.py`
implements the canonical construction:

```python
def construct_state_predicate_id(
    *,
    label: str,
    threshold_value_double: float | None,
    operator_comparison: str | None,
    reference_predicate_label: str | None,
) -> str:
    # format: "<label>|t=<threshold>|op=<op>|ref=<ref>"
    # missing fields render as "t=None" etc. — float coercion ensures
    # "100.0" not "100" for integer thresholds from YAML.
    thr = f"t={float(threshold_value_double)}" if threshold_value_double is not None else "t=None"
    op = f"op={operator_comparison}" if operator_comparison else "op=None"
    ref = f"ref={reference_predicate_label}" if reference_predicate_label else "ref=None"
    return f"{label}|{thr}|{op}|{ref}"
```

Callers must coerce numeric inputs to float for consistent id format
(`str(1)` vs `str(1.0)` differ — YAML int vs Python float).

---

## 3. `@key` / `@unique` unsupported on `double`-valued attributes

**Symptom.** Adding `@key` or `@unique` to an attribute of
`value double` fails with SVL26 or SVL27.

**Root cause.** TypeDB 3.x explicitly disallows `@key` and `@unique` on
doubles (floating-point equality semantics).

**Workaround.** Use `value string` or `value integer` for keyed
attributes. If you need a real-valued threshold alongside a unique
identifier, split into two attributes: a string id (keyed) and a double
value (not keyed), stored together on the same entity.

---

## 4. `@abstract` cannot combine with `sub` in one statement

**Symptom.**

```typeql
entity instrument_class @abstract sub object_class,   # syntax error
    owns ...;
```

**Workaround.** Declare abstract in one statement, parent in a second:

```typeql
entity instrument_class @abstract;
entity instrument_class sub object_class,
    owns ...;
```

Both seen in `app/data/schema_v4_deontic.tql` §4.3 and §4.4.

---

## 5. Polymorphic `isa` matches subtypes; `isa!` is exact

**Symptom.** A match-insert targeting an abstract type unexpectedly
inserts edges for every concrete subtype that sub'd from it — counts
come out higher than expected (Prompt 07 Part 2 saw 62 edges instead of
26).

**Failing example.**

```typeql
match $e isa blocker_exception;   # matches abstract + all 6 subtypes
(blocker: $b, exception: $e) isa blocker_has_exception;
```

**Workaround.** Use `isa!` when you mean "instances of exactly this
type":

```typeql
match $e isa! nonexclusive_license_exception;
```

Or, for seeding paths where the abstract match is correct but you want
only one row per concrete instance, dedupe by an identity attribute
(e.g., `exception_id`) in the Python caller. `deontic_projection.py`
`_project_jcrew_defeaters` does this:

```python
# Dedupe by exception_id (the polymorphic match returns each instance
# once per matching isa type — abstract + concrete = 2 rows).
seen = set()
for r in rows:
    eid = r.get("eid").as_attribute().get_value()
    if eid in seen:
        continue
    seen.add(eid)
    ...
```

---

## 6. Multi-statement `insert` and `match-insert` files need per-statement execution

**Symptom.** A TQL file with multiple `insert` or `match...insert`
statements piped through a single `tx.query()` call silently executes
only the first block. No error; subsequent blocks disappear.

**Root cause.** TypeDB 3.x WRITE transactions bundle multiple
statements for parser scope, but the execution engine commits only the
first write block per query call.

**Workaround.** Split the file on statement boundaries and execute
each in its own query call (preferably its own transaction so one
failure doesn't roll back the others). See
`app/services/seed_loader.py` — `_load_write_file` pattern:

```python
for statement in split_tql_statements(file_contents):
    tx = driver.transaction(db_name, TransactionType.WRITE)
    try:
        tx.query(statement).resolve()
        tx.commit()
    except Exception as e:
        logger.warning("seed statement failed: %s", e)
    finally:
        if tx.is_open():
            tx.close()
```

The v3-era `_load_mixed_tql_file` parser has a subtlety: when scanning
for the next `insert` token, if the current block is a match-insert
pair, the `insert` line appearing mid-block is the pair's consequent,
not a new top-level statement. Don't switch to "insert block mode" on
seeing `insert` while in match-scanning mode.

---

## 7. Underscore-prefixed function names rejected

**Symptom.** Defining `fun _private_helper($x: integer) -> integer:`
parses but the function is unusable; queries referencing it resolve to
missing-symbol errors.

**Workaround.** Don't prefix function names with underscore. Use a
plain-prefixed alternative (`helper_`, `compute_`, `check_`, etc.).

---

## 8. Function body single-reduce limit

**Symptom.** Functions returning aggregate values can include exactly
one `reduce` expression in the body. Multi-reduce functions fail to
parse.

**Workaround.** Compose via helper functions — one reduce per
function, chain the results via `let` bindings.

---

## 9. Value/attribute variable boundary across function parameters

**Symptom.** A query function that passes a value between
attribute-typed parameters fails with `UncomparableValues` or similar.
Pattern:

```typeql
fun check_threshold($n: norm, $threshold: double) -> { boolean }:
match
  $n has cap_usd $c;
  $c == $threshold;         # fails: $c is attribute, $threshold is value
return ...;
```

**Workaround.** Read the attribute into a value variable before the
comparison, or compare attributes to attributes. See
`app/data/deontic_*_functions.tql` for the `has attr $x; $x == $param;`
pattern — `$x` here is the attribute handle; the comparison against
the parameter-value works because TypeQL resolves attribute-to-value
comparisons when both sides' types are compatible.

---

## 10. `define` is idempotent; `redefine` for type hierarchy changes

**Symptom.** `redefine` fails when the type is not yet in the schema.

**Workaround.** Use `define` for first-time additions — it's
idempotent per the 3.x spec. Only use `redefine` for changes to
existing types (e.g., adding a role alias to a relation that's already
defined). Schema hierarchy changes (changing an entity's parent) still
require a full rebuild; `init_schema_v4` has a `--schema-only` flag
that preserves extraction data while rebuilding types.

---

## 11. Delete queries silently fail when the referenced type isn't in the schema

**Symptom.** A `delete` query over an entity or attribute type that
hasn't been added yet silently succeeds (removes nothing) and returns
no error. If wrapped in a transaction with other deletes, a failure
here can roll back the whole tx.

**Workaround.** Run each delete in its own transaction so one failure
doesn't abort the others. `clear_v4_projection_for_deal` in
`deontic_projection.py` does this — three separate transactions for
conditions, norms, and defeaters, each independently commitable.

---

## 12. `str(int)` vs `str(float)` differ — coerce numeric id components

**Symptom.** A state_predicate or other composite-id lookup returns
zero rows even though the data was inserted successfully. Diagnostics
show the id used at lookup differs from the id used at insert by a
trailing `.0` or missing decimal.

**Root cause.** YAML loader returns `100` (int) for integer-valued
thresholds; Python float-typed callers pass `100.0`. The composite-id
string concatenation renders them differently.

**Workaround.** Coerce numeric components to float in the id
construction function (see pattern 2 above). `construct_state_predicate_id`
does `float(threshold_value_double)` unconditionally for this reason.

---

## 13. `load_dotenv(override=True)` required for CLI invocations

**Symptom.** Running a module via `py -3.12 -m app.services.X` fails
with missing-API-key errors even when `.env` is present.

**Root cause.** CLI invocation may start in a working directory where
Python's default `load_dotenv` call doesn't find or doesn't override
inherited environment. If a stale `ANTHROPIC_API_KEY` is already set
via shell startup, `load_dotenv(override=False)` leaves it in place.

**Workaround.** In every CLI entry point, pass `override=True`:

```python
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path("C:/Users/olive/ValenceV3/.env"), override=True)
```

---

## 14. Role + edge-attribute access via `links` form

**Symptom.** The intuitive form combining an `isa` on a relation tuple
with attribute access on that same relation variable fails at type
inference:

```tql
match
  $rel (contributor: $n, pool: $parent) isa norm_contributes_to_capacity;
  try { $rel has aggregation_direction $dir; };
select $n, $parent, $dir;
```

Error: `[REP1] The variable 'rel' cannot be declared as both a 'Object'
and as a 'ThingType'.`

**Root cause.** In TypeDB 3.x, the tuple-form `$rel (role: $var, ...) isa
$reltype` declares `$rel` as a type-bound relation (ThingType scope),
while `$rel has $attr` requires `$rel` to be an instance (Object scope).
The parser cannot reconcile both assertions on the same variable.

**Workaround.** Use the `links` form to separate relation identification
from role binding:

```tql
match
  $rel isa norm_contributes_to_capacity,
      links (contributor: $n, pool: $parent);
  try { $rel has aggregation_direction $dir; };
select $n, $parent, $dir;
```

`$rel isa <reltype>` establishes `$rel` as a concrete relation instance;
`links (role: $var, ...)` then binds role-players on that instance
without re-scoping `$rel`. Attribute access via `has` then works.

**Where needed:** any query reading both role players AND edge
attributes of the same relation. Four call sites in
`app/services/operations.py` (Prompt 11 commits) use this pattern. The
two-query fallback (one for structure, one for attributes) is the only
alternative when the `links` form isn't available — see the
`_describe_condition` children-walk in operations.py for that variant.

**Prior occurrence.** v3's `app/services/graph_storage.py` uses this
form for every relation query that reads both role-bound entities and
edge attributes (lines 1487+, 2074+, 2099+ etc.).

---

## 15. Disambiguate role names up-front when adding new relations

**Discipline (not a trap, but a policy).** Generic role names like
`source`, `target`, `event`, `condition`, `norm` invite collisions when
later relations try to re-use them. The collision either silently
breaks queries (Pattern #14 territory) or forces awkward `@role-alias`
patches after the fact.

**Evidence.** The `condition` role on `norm_has_condition`,
`condition_references_predicate`, and `condition_has_child` already
required a role-alias workaround (`norm_has_condition` uses `root` as
its alias) — see `docs/v4_known_gaps.md` §"Role-name collisions on
condition." Phase B avoided rerunning that pain by disambiguating the
moment new relations were added.

**Phase B examples (committed 2026-04-27).**

`norm_reallocates_capacity_from`:

```tql
relation norm_reallocates_capacity_from,
    relates reallocation_receiver,    # disambiguated: not just "receiver"
    relates reallocation_source,      # disambiguated: not just "source"
    owns reallocation_mechanism,
    owns reduction_direction;
norm plays norm_reallocates_capacity_from:reallocation_receiver;
norm plays norm_reallocates_capacity_from:reallocation_source;
```

`event_provides_proceeds_to_norm`:

```tql
relation event_provides_proceeds_to_norm,
    relates proceeds_event,           # disambiguated: not just "event"
    relates proceeds_target_norm,     # disambiguated: not just "target_norm"
    owns proceeds_flow_kind,
    owns proceeds_flow_conditions;
event_class plays event_provides_proceeds_to_norm:proceeds_event;
norm plays event_provides_proceeds_to_norm:proceeds_target_norm;
```

The rule of thumb: **prefix the role with a relation-specific qualifier**
(`reallocation_*`, `proceeds_*`) when the unqualified name (`source`,
`event`, `target_norm`) would land in TypeDB's role-name namespace
shared across relations. Verify before declaring with:

```bash
grep -rn "relates <candidate_name>" app/data/*.tql
```

If the grep returns hits for any other relation, disambiguate.

---

## Index of known schema-time blockers

For quick reference, the annotations and constructs that don't work in
TypeDB 3.x (pilot-era):

| Construct | Status | Workaround |
|---|---|---|
| `@key` on multiple attributes (composite key) | not supported | pipe-delimited string id attribute |
| `@unique` on multiple attributes | not supported | same |
| `@key` on `value double` | not supported (SVL26/27) | cast to string or integer |
| `@unique` on `value double` | not supported | same |
| `@abstract` + `sub` in one statement | not supported | two statements |
| `_` prefix on function names | rejected at resolve time | non-prefix naming |
| Multi-reduce function body | not supported | compose helpers |
| `isa` for exact-type match | polymorphic (matches subtypes) | use `isa!` |
| `try { }` with attr not in type's owns | INF11 at compile time | reference only owned attributes |
| Multiple `match-insert` in one `query()` call | silently drops after the first | per-statement execution |

---

## How to add a new entry

If a new gotcha fires in production:

1. Reproduce the failure with a minimal TQL example.
2. Add a numbered section above with: symptom, failing example, root
   cause, workaround, prior occurrence.
3. If the failure should never recur, add an A6 invariant check in
   `validation_harness.py` covering the class of regression.
4. Update the index at the bottom if the gotcha blocks a schema
   construct.
