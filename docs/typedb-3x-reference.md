# TypeDB 3.x Quick Reference for Valence

> **Purpose:** Prevent TypeDB 2.x syntax errors in Valence development.
> **Last Updated:** February 2026
> **Audience:** Any AI assistant or developer working on the Valence codebase

---

## Official Documentation — ALWAYS Check These First

If you are unsure about any TypeDB syntax, query pattern, or API usage, **consult the official docs before writing code**:

| Topic | URL |
|-------|-----|
| **TypeQL Language Guide** (start here) | https://typedb.com/docs/typeql-reference/ |
| **Schema: Define/Undefine/Redefine** | https://typedb.com/docs/typeql-reference/schema/define |
| **Functions (replaced rules)** | https://typedb.com/docs/typeql-reference/functions/ |
| **Functions vs Rules migration** | https://typedb.com/docs/typeql-reference/functions/functions-vs-rules/ |
| **Writing functions** | https://typedb.com/docs/typeql-reference/functions/writing/ |
| **Defining functions in schema** | https://typedb.com/docs/manual/schema/functions |
| **Data model & query semantics** | https://typedb.com/docs/typeql-reference/data-model/ |
| **Fetch stage (JSON output)** | https://typedb.com/docs/typeql/pipelines/fetch |
| **Keyword glossary** | https://typedb.com/docs/typeql-reference/keywords/ |
| **Python gRPC driver** | https://typedb.com/docs/reference/typedb-grpc-drivers/python/ |
| **2.x → 3.x: what changed** | https://typedb.com/docs/reference/typedb-2-vs-3/diff/ |
| **2.x → 3.x: migration process** | https://typedb.com/docs/reference/typedb-2-vs-3/process/ |
| **Transactions** | https://typedb.com/docs/academy/6-building-applications/6.3-transactions/ |
| **Get started (schema basics)** | https://typedb.com/docs/home/get-started/schema/ |
| **HTTP API reference** | https://typedb.com/docs/reference/typedb-http-api/ |

---

## The Changes That Bite You

### 1. Type Declarations Use Explicit Kinds

TypeDB 3.x requires explicit `entity`, `relation`, `attribute` keywords. No more `sub entity`.

```
# ✗ WRONG (2.x)
person sub entity, owns name;
friendship sub relation, relates friend;
name sub attribute, value string;

# ✓ CORRECT (3.x)
entity person, owns name;
relation friendship, relates friend;
attribute name, value string;
```

**Valence example** (from `schema_unified.tql`):
```tql
entity deal,
    owns deal_id @key,
    owns deal_name,
    owns borrower_name;

attribute deal_id, value string;
attribute deal_name, value string;

relation deal_jcrew_analysis,
    relates deal,
    relates provision_analysis,
    relates definition_analysis,
    relates interaction_analysis;
```

Subtyping still uses `sub`:
```tql
entity builder_basket sub rp_provision;
entity ratio_basket sub rp_provision;
attribute page_id sub id;
```

### 2. No Sessions — Transactions Directly

TypeDB 3.x removed sessions entirely. Open transactions directly on the driver.

```python
# ✗ WRONG (2.x)
with driver.session(database, SessionType.DATA) as session:
    with session.transaction(TransactionType.READ) as tx:
        result = tx.query.get("match $d isa deal; get;")

# ✓ CORRECT (3.x)
tx = driver.transaction(database, TransactionType.READ)
try:
    result = tx.query("match $d isa deal; select $d;").resolve()
    rows = list(result.as_concept_rows())
finally:
    tx.close()
```

**Three transaction types** (no more schema-read / schema-write split):
- `TransactionType.READ` — read data, call functions
- `TransactionType.WRITE` — read + insert/delete/update data
- `TransactionType.SCHEMA` — define/undefine/redefine types and functions

**Valence example** (from `typedb_client.py`):
```python
from typedb.driver import TransactionType

tx = typedb_client.driver.transaction(
    settings.typedb_database, TransactionType.READ
)
try:
    result = tx.query("""
        match $q isa ontology_question, has question_id $qid;
        select $qid;
    """).resolve()
    for row in result.as_concept_rows():
        qid = row.get("qid").as_attribute().get_value()
finally:
    tx.close()
```

### 3. Unified Query API

No more `tx.query().get()`, `tx.query().insert()`, `tx.query().define()`. Everything goes through `tx.query()`.

```python
# ✗ WRONG (2.x)
tx.query().get("match $d isa deal; get;")
tx.query().insert("insert $d isa deal, has deal_id 'abc';")
tx.query().define("define entity foo;")

# ✓ CORRECT (3.x)
tx.query("match $d isa deal; select $d;").resolve()
tx.query("insert $d isa deal, has deal_id 'abc';").resolve()
tx.query("define entity foo;").resolve()
```

### 4. `select` Replaces `get`

```
# ✗ WRONG (2.x)
match $d isa deal, has deal_name $n; get $n;

# ✓ CORRECT (3.x)
match $d isa deal, has deal_name $n; select $n;
```

### 5. All Variables Use `$` — No More `?`

Value variables no longer use `?`. Use `$` with `let` for computed values.

```
# ✗ WRONG (2.x)
match $d isa deal, has deal_id $did;
?count = count;

# ✓ CORRECT (3.x)
match $d isa deal, has deal_id $did;
# Use reduce for aggregation:
reduce $count = count;
```

### 6. `fetch` Returns JSON Documents

`fetch` is the primary way to get structured output. It replaces the 2.x pattern of `get` + client-side attribute extraction.

```tql
# Return JSON with deal details
match
    $d isa deal, has deal_id "deal-123";
fetch {
    "deal": $d.*
};

# Custom JSON structure
match
    $d isa deal, has deal_id $did, has deal_name $name;
    $a isa jcrew_provision_analysis, has deal_ref $did,
        has jcrew_blocker_exists $blocker;
fetch {
    "deal_name": $name,
    "has_blocker": $blocker
};
```

### 7. Annotations Use `@` Syntax

```
# ✗ WRONG (2.x)
person sub entity, abstract;
person owns name, key;
email sub attribute, value string, regex ".*@.*";

# ✓ CORRECT (3.x)
entity person @abstract;
person owns name @key;
attribute email, value string; # regex is now @regex(".*@.*")
```

Common annotations in Valence:
```tql
entity deal, owns deal_id @key;           # unique key
entity rp_provision @abstract;            # abstract base type
deal owns deal_date @card(0..1);          # optional (0 or 1)
user owns status @card(0..);              # unbounded cardinality
```

### 8. Functions Replace Rules

This is the biggest change. TypeDB 3.x dropped the `rule` keyword entirely.

```
# ✗ WRONG (2.x) — will fail to load
define
rule detect_pattern_no_blocker:
    when {
        $a isa jcrew_provision_analysis, has jcrew_blocker_exists false;
    } then {
        $a has pattern_no_blocker true;
    };

# ✓ CORRECT (3.x) — use fun
define
fun has_pattern_no_blocker($ref: string) -> boolean:
    match
        $a isa jcrew_provision_analysis, has deal_ref $dr,
            has unsub_designation_permitted true,
            has jcrew_blocker_exists false;
        $dr == $ref;
    return check;
```

**Key differences:**

| Aspect | Rules (2.x) | Functions (3.x) |
|--------|-------------|-----------------|
| Keyword | `rule name: when { } then { };` | `fun name($args) -> return_type:` |
| Execution | Implicit (inference toggle) | Explicit (called in queries) |
| Side effects | Silently adds attributes | Returns values, no mutation |
| Staleness | Can be stale if data changes | Always fresh (computed on demand) |
| Composition | Rules can't call rules | Functions can call functions |

**Function return types:**

```tql
# Boolean check — returns true if match has results
fun is_vulnerable($ref: string) -> boolean:
    match $a isa jcrew_provision_analysis, has deal_ref $dr;
        $dr == $ref;
    return check;

# Single scalar value
fun get_cap($ref: string) -> long:
    match $a isa jcrew_provision_analysis, has deal_ref $dr,
        has unsub_hard_cap_dollars $cap;
        $dr == $ref;
    return first $cap;

# Stream of values
fun get_all_deal_refs() -> { string }:
    match $a isa jcrew_provision_analysis, has deal_ref $ref;
    return { $ref };
```

**GOTCHA — Function parameters vs attribute variables:**

Function parameters like `$ref: string` are **value variables**. But `has deal_ref $ref` binds `$ref` as an **attribute instance**. The same variable cannot be both. Use a separate variable for the attribute binding:

```tql
# ✗ WRONG — $ref is both a value param and attribute binding
fun my_func($ref: string) -> boolean:
    match
        $a isa entity_type, has deal_ref $ref;
    return check;

# ✓ CORRECT — $dr binds the attribute, $dr == $ref compares values
fun my_func($ref: string) -> boolean:
    match
        $a isa entity_type, has deal_ref $dr;
        $dr == $ref;
    return check;
```

**GOTCHA — Return type is `boolean`, not `bool`:**
```tql
# ✗ WRONG
fun my_func($ref: string) -> bool:

# ✓ CORRECT
fun my_func($ref: string) -> boolean:
```

**Calling functions in queries:**

```tql
# Boolean function — use == comparison
match
    $d isa deal, has deal_id $did;
    true == has_pattern_no_blocker($did);
select $did;

# Scalar function — use let assignment
match
    $d isa deal, has deal_id $did;
    let $cap = get_cap($did);
select $did, $cap;

# Stream function — use let ... in
match
    let $ref in get_all_deal_refs();
select $ref;
```

**Function composition** (functions calling functions):
```tql
fun has_interaction_blocker_scope_misses_chain($ref: string) -> boolean:
    match
        $pa isa jcrew_provision_analysis, has deal_ref $dr,
            has blocker_applies_at_designation_only true;
        $dr == $ref;
        true == has_pattern_chain_pathway_open($ref);
    return check;
```

**Loading functions** (schema transaction):
```python
tx = driver.transaction(database, TransactionType.SCHEMA)
try:
    tx.query(functions_tql).resolve()
    tx.commit()
except Exception as e:
    tx.close()
    raise
```

### 9. `or` Syntax in Patterns

Disjunction works the same way, but note the braces:

```tql
match
    $a isa jcrew_provision_analysis, has deal_ref $ref;
    {
        $a has inv_direct_lp_to_unsub_uncapped true;
    } or {
        $a has inv_lp_to_non_guarantor_rs_uncapped true;
        $a has inv_rs_to_unsub_uncapped true;
    } or {
        $a has inv_non_lp_rs_to_unsub_unlimited true;
    };
select $ref;
```

### 10. `not` for Negation

```tql
# Find deals WITHOUT a blocker
match
    $d isa deal, has deal_id $did;
    $a isa jcrew_provision_analysis, has deal_ref $did;
    not { $a has jcrew_blocker_exists true; };
select $did;
```

### 11. `try` for Optional Matches

If an attribute might not exist, wrap in `try`:

```tql
match
    $a isa jcrew_provision_analysis, has deal_ref $ref;
    $a has jcrew_blocker_exists $blocker;
    try { $a has unsub_hard_cap_dollars $cap; };
select $ref, $blocker, $cap;
```

### 12. `put` for Upsert

`put` tries to match first; if no match, it inserts:

```tql
# Insert deal if it doesn't exist, otherwise match the existing one
put $d isa deal, has deal_id "deal-123", has deal_name "Duck Creek";
```

### 13. Reading Concept Rows in Python

```python
result = tx.query("""
    match
        $q isa ontology_question,
            has question_id $qid,
            has question_text $text,
            has answer_type $atype;
    select $qid, $text, $atype;
""").resolve()

for row in result.as_concept_rows():
    qid = row.get("qid").as_attribute().get_value()
    text = row.get("text").as_attribute().get_value()
    atype = row.get("atype").as_attribute().get_value()
```

For entities (not attributes):
```python
result = tx.query("match $d isa deal; select $d;").resolve()
for row in result.as_concept_rows():
    deal = row.get("d")
    # To get attributes, run a separate fetch or match query
```

### 14. Relation Syntax

Relation role players are specified with parentheses:

```tql
# Insert a relation
match
    $p isa rp_provision, has provision_id "builder-001";
    $c isa builder_source, has concept_id "bs_cni";
insert
    (provision: $p, concept: $c) isa concept_applicability,
        has applicability_status "INCLUDED",
        has source_text "50% of Consolidated Net Income";
```

```tql
# Match a relation
match
    (provision: $p, concept: $c) isa concept_applicability;
    $p has provision_id $pid;
    $c has concept_name $cname;
select $pid, $cname;
```

---

## Valence-Specific Patterns

### SSoT Principle

TypeDB is the single source of truth. Never hardcode field lists, question lists, or concept options in Python or TypeScript. Always query TypeDB:

```python
# ✓ CORRECT — query TypeDB for segment types
segments = get_segment_types()  # queries document_segment_type entities

# ✗ WRONG — hardcoded list
SEGMENTS = ["definitions", "negative_cov_rp", "investments", ...]
```

### Polymorphic Introspection Pattern

Query entity subtypes dynamically:

```tql
# Find all subtypes of legal_concept
match
    $type sub legal_concept;
select $type;

# Find all concept instances of any subtype
match
    $c isa legal_concept,
        has concept_id $cid,
        has concept_name $name;
select $cid, $name;
```

### Insert with Match (common in extraction storage)

```tql
match
    $p isa builder_basket, has provision_id "builder-deal123";
insert
    $p has builder_starter_amount 75000000,
       has source_text "the greater of $75,000,000 and 30% of EBITDA",
       has source_page 142,
       has confidence "high";
```

---

## Common Mistakes Checklist

Before committing any TypeDB-related code, verify:

- [ ] No `sub entity` / `sub relation` / `sub attribute` — use `entity X`, `relation X`, `attribute X`
- [ ] No `rule` keyword — use `fun` instead
- [ ] No `SessionType` — transactions open directly on driver
- [ ] No `tx.query().get()` / `tx.query().insert()` — use `tx.query("...").resolve()`
- [ ] No `get $var;` — use `select $var;`
- [ ] No `?var` — use `$var` with `let` for computed values
- [ ] No `abstract` without `@` — use `@abstract`
- [ ] No `key` without `@` — use `@key`
- [ ] Functions use `-> boolean:` not `-> bool:`
- [ ] Function params don't collide with attribute bindings (use `$dr == $ref;` pattern)
- [ ] Functions loaded in `TransactionType.SCHEMA` transactions
- [ ] Data inserted in `TransactionType.WRITE` transactions
- [ ] Data read in `TransactionType.READ` transactions
- [ ] Always call `.resolve()` after `tx.query()`
- [ ] Always close transactions in a `try/finally` block
- [ ] Attributes cannot own attributes in 3.x

---

## When In Doubt

1. **Check the official docs first** — links at the top of this document
2. **Look at existing Valence code** — `schema_unified.tql`, `init_schema.py`, `extraction.py`
3. **Test in a schema transaction** before deploying — `TransactionType.SCHEMA` for types/functions
4. **Check the migration guide** — https://typedb.com/docs/reference/typedb-2-vs-3/diff/
