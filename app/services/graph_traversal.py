"""
Graph Traversal — builds entity context via single polymorphic TypeDB fetch.

All entities, all attributes, all annotations, all children in one query.
TypeDB schema is the single source of truth — no hardcoded attribute lists.
"""
import json
import logging
import time
from typing import List, Optional, Tuple

from typedb.driver import TransactionType

from app.config import settings
from app.services.trace_collector import TraceCollector
from app.services.typedb_client import typedb_client

logger = logging.getLogger(__name__)

# ═════════════════════════════════════════════════════════════════════════════
# SCHEMA INTROSPECTION — child relation detection (SSoT)
# ═════════════════════════════════════════════════════════════════════════════

_provision_has_children_cache: dict = {}


def _provision_has_child_relations(provision_type: str) -> bool:
    """Check if any entity type linked to this provision type has child relations.

    Introspects schema via TypeDB: finds entity types extracted from this
    provision type, then checks if any play a parent role in an
    entity_has_child sub-relation.

    Cached after first call per provision_type.
    """
    if provision_type in _provision_has_children_cache:
        return _provision_has_children_cache[provision_type]

    if not typedb_client.driver:
        _provision_has_children_cache[provision_type] = False
        return False

    try:
        tx = typedb_client.driver.transaction(
            settings.typedb_database, TransactionType.READ
        )
        try:
            # Step 1: Get entity types linked to this provision type
            rows = list(tx.query(
                f'match $p isa {provision_type}; '
                f'(provision: $p, extracted: $e) isa $rel; '
                f'$rel sub provision_has_extracted_entity; '
                f'$e isa! $etype; '
                f'let $type_name = label($etype); '
                f'select $type_name;'
            ).resolve().as_concept_rows())
            entity_types = set()
            for r in rows:
                tn = r.get("type_name")
                if tn:
                    entity_types.add(tn.as_value().get())

            # Step 2: Get entity_has_child sub-relations
            rows2 = list(tx.query(
                'match relation $rel; $rel sub entity_has_child; '
                'not { $rel label entity_has_child; }; '
                'let $rname = label($rel); '
                'select $rname;'
            ).resolve().as_concept_rows())
            child_rels = set()
            for r in rows2:
                rn = r.get("rname")
                if rn:
                    child_rels.add(rn.as_value().get())

            # Step 3: Check if any parent role types overlap with our entity types
            has_children = False
            for child_rel in child_rels:
                rows3 = list(tx.query(
                    f'match relation $rel label {child_rel}; '
                    f'$rel relates $parent_role; '
                    f'entity $etype; $etype plays $parent_role; '
                    f'let $etype_name = label($etype); '
                    f'select $etype_name;'
                ).resolve().as_concept_rows())
                parent_types = set()
                for r in rows3:
                    pn = r.get("etype_name")
                    if pn:
                        parent_types.add(pn.as_value().get())
                if entity_types & parent_types:
                    has_children = True
                    break
        finally:
            if tx.is_open():
                tx.close()

        _provision_has_children_cache[provision_type] = has_children
        logger.info(f"Provision {provision_type} has child relations: {has_children}")
        return has_children

    except Exception as e:
        logger.warning(f"Child relation introspection failed for {provision_type}: {e}")
        # Safe default: assume children exist, use full query
        _provision_has_children_cache[provision_type] = True
        return True


# ═════════════════════════════════════════════════════════════════════════════
# POLYMORPHIC FETCH QUERY
# ═════════════════════════════════════════════════════════════════════════════

_FETCH_QUERY = '''
match
    $p isa {prov_type}, has provision_id "{pid}";
    (provision: $p, extracted: $e) isa $rel;
    $rel sub provision_has_extracted_entity;
    let $rel_name = label($rel);
    $rel_name != "provision_has_extracted_entity";
    $e isa! $etype;
    let $type_name = label($etype);
fetch {{
    "relation": $rel_name,
    "type_name": $type_name,
    "attributes": {{ $e.* }},
    "annotations": [
        match
            let $an, $qt in get_entity_annotations($type_name);
        fetch {{ "attribute": $an, "annotation": $qt }};
    ],
    "children": [
        match
            $child_link isa $child_rel, links (parent: $e, child: $child);
            $child_rel sub entity_has_child;
            $child isa! $ctype;
            let $child_type_name = label($ctype);
            let $child_rel_name = label($child_rel);
        fetch {{
            "child_relation": $child_rel_name,
            "child_type": $child_type_name,
            "child_attributes": {{ $child.* }},
            "child_annotations": [
                match
                    let $can, $cqt in get_entity_annotations($child_type_name);
                fetch {{ "attribute": $can, "annotation": $cqt }};
            ]
        }};
    ],
    "links": [
        match
            $link isa $link_type, links ($my_role: $e, $their_role: $linked);
            not {{ $link_type sub provision_has_extracted_entity; }};
            not {{ $link_type sub entity_has_child; }};
            $linked isa! $linked_etype;
            let $link_name = label($link_type);
            let $linked_type_name = label($linked_etype);
            let $my_role_name = label($my_role);
            let $their_role_name = label($their_role);
        fetch {{
            "link_relation": $link_name,
            "my_role": $my_role_name,
            "their_role": $their_role_name,
            "linked_type": $linked_type_name,
            "linked_attributes": {{ $linked.* }},
            "relation_attributes": {{ $link.* }}
        }};
    ]
}};
'''


# Fallback for provision types whose entities don't support children/links
# subqueries (e.g. MFN entities have no entity_has_child relationships,
# causing TypeDB type-inference errors in the full query).
_FETCH_QUERY_SIMPLE = '''
match
    $p isa {prov_type}, has provision_id "{pid}";
    (provision: $p, extracted: $e) isa $rel;
    $rel sub provision_has_extracted_entity;
    let $rel_name = label($rel);
    $rel_name != "provision_has_extracted_entity";
    $e isa! $etype;
    let $type_name = label($etype);
fetch {{
    "relation": $rel_name,
    "type_name": $type_name,
    "attributes": {{ $e.* }},
    "annotations": [
        match
            let $an, $qt in get_entity_annotations($type_name);
        fetch {{ "attribute": $an, "annotation": $qt }};
    ]
}};
'''


# ═════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═════════════════════════════════════════════════════════════════════════════

def get_provision_entities(
    deal_id: str,
    provision_type: str = "rp_provision",
    trace: TraceCollector = None,
) -> Tuple[List[dict], str]:
    """Fetch all entities for a provision via polymorphic TypeDB query.

    Returns (docs, context_string) where docs is the raw list of entity
    documents and context_string is the formatted text for Claude synthesis.
    """
    if not typedb_client.driver:
        return [], "(TypeDB not connected)"

    suffix = provision_type.replace("_provision", "")
    provision_id = f"{deal_id}_{suffix}"

    if trace:
        trace.provision_id = provision_id

    try:
        start = time.time()
        tx = typedb_client.driver.transaction(
            typedb_client.database, TransactionType.READ
        )
        try:
            # ── Provision-level attributes (SSoT: reads whatever the provision has) ──
            prov_header = ""
            try:
                prov_attr_query = (
                    f'match $p isa {provision_type}, has provision_id "{provision_id}"; '
                    f'$p has $attr; '
                    f'let $attr_type = label(type($attr)); '
                    f'select $attr_type, $attr;'
                )
                prov_rows = list(tx.query(prov_attr_query).resolve().as_concept_rows())
                prov_attrs = {}
                for row in prov_rows:
                    attr_type = row.get("attr_type").as_value().get()
                    attr_val = row.get("attr").as_attribute().get_value()
                    if attr_type != "provision_id":
                        prov_attrs[attr_type] = attr_val
                if prov_attrs:
                    lines = [f"## PROVISION: {provision_type} ({provision_id})"]
                    for k, v in sorted(prov_attrs.items()):
                        lines.append(f"- **{k}**: {v}")
                    prov_header = "\n".join(lines) + "\n\n"
            except Exception as e:
                logger.warning(f"Provision attribute query failed: {e}")

            # SSoT: introspect schema to decide if children subquery is needed
            if _provision_has_child_relations(provision_type):
                query = _FETCH_QUERY.format(prov_type=provision_type, pid=provision_id)
            else:
                query = _FETCH_QUERY_SIMPLE.format(prov_type=provision_type, pid=provision_id)
            answer = tx.query(query).resolve()
            docs = list(answer.as_concept_documents())
        finally:
            tx.close()
        duration_ms = (time.time() - start) * 1000

        if trace:
            trace.add_query(
                name="polymorphic_entity_fetch",
                query=query,
                row_count=len(docs),
                duration_ms=duration_ms,
                sample_rows=docs[:3] if docs else [],
            )
            trace.entity_count = len(docs)

        if not docs:
            return [], "(No Channel 3 entities found for this provision)"

        entity_json = json.dumps(docs, indent=2, default=str)
        context = f"{prov_header}## ENTITY DATA\n\n{entity_json}"

        if trace:
            trace.entity_context = context
            trace.entity_context_chars = len(context)

        return docs, context
    except Exception as e:
        logger.error(f"Polymorphic entity fetch failed: {e}")
        return [], "(TypeDB query failed)"


def get_rp_entities(deal_id: str, trace: TraceCollector = None) -> str:
    """Backward-compatible wrapper — returns only the context string."""
    _, context = get_provision_entities(deal_id, "rp_provision", trace)
    return context


def get_cross_covenant_entities(
    deal_id: str,
    source_provision_type: str,
    trace: TraceCollector = None,
) -> Tuple[List[dict], str]:
    """Walk provision_cross_reference to load entities from linked provisions.

    UNIDIRECTIONAL: walks source→target only. The extraction pipeline creates
    (source_provision: mfn, target_provision: rp), so this walks MFN→RP.
    RP questions should NOT call this — they would find no outgoing edges.

    Reuses get_provision_entities() for the actual entity fetch (SSoT).
    """
    if not typedb_client.driver:
        return [], ""

    suffix = source_provision_type.replace("_provision", "")
    source_pid = f"{deal_id}_{suffix}"

    try:
        start = time.time()
        tx = typedb_client.driver.transaction(
            typedb_client.database, TransactionType.READ
        )
        try:
            # Find linked provisions via provision_cross_reference (source→target)
            query = f"""
                match
                    $source isa {source_provision_type},
                        has provision_id "{source_pid}";
                    (source_provision: $source, target_provision: $target)
                        isa provision_cross_reference;
                    $target has provision_id $target_pid;
                    $target isa! $target_type;
                    let $ttype = label($target_type);
                select $target_pid, $ttype;
            """
            result = list(tx.query(query).resolve().as_concept_rows())
        finally:
            tx.close()

        if not result:
            if trace:
                trace.add_query(
                    name="cross_covenant_lookup",
                    query=query,
                    row_count=0,
                    duration_ms=(time.time() - start) * 1000,
                )
            return [], ""

        # For each linked provision, fetch its entities
        all_cross_docs = []
        for row in result:
            target_pid = row.get("target_pid").as_attribute().get_value()
            target_type = row.get("ttype").as_value().get_value()

            # Extract deal_id from provision_id (format: "{deal_id}_{suffix}")
            target_deal_id = target_pid.rsplit("_", 1)[0]

            target_docs, _ = get_provision_entities(
                target_deal_id, target_type, trace=None
            )

            # Mark each entity as cross-covenant sourced
            for doc in target_docs:
                doc["source"] = "cross_reference"
            all_cross_docs.extend(target_docs)

        duration_ms = (time.time() - start) * 1000

        if trace:
            trace.add_query(
                name="cross_covenant_fetch",
                query=f"provision_cross_reference from {source_pid}",
                row_count=len(all_cross_docs),
                duration_ms=duration_ms,
            )

        if not all_cross_docs:
            return [], ""

        cross_json = json.dumps(all_cross_docs, indent=2, default=str)
        context = (
            "## CROSS-COVENANT ENTITIES\n\n"
            "These entities come from linked provisions via "
            "provision_cross_reference. They provide context from other "
            "parts of the credit agreement that interact with the "
            "provision being analyzed.\n\n"
            f"{cross_json}"
        )

        return all_cross_docs, context

    except Exception as e:
        logger.warning(f"Cross-covenant entity fetch failed: {e}")
        return [], ""
