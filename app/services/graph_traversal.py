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

from app.services.trace_collector import TraceCollector
from app.services.typedb_client import typedb_client

logger = logging.getLogger(__name__)


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
            query = _FETCH_QUERY.format(prov_type=provision_type, pid=provision_id)
            try:
                answer = tx.query(query).resolve()
                docs = list(answer.as_concept_documents())
            except Exception as qe:
                # TypeDB type-inference fails if entity types don't support
                # children/links subqueries (e.g. MFN entities have no
                # entity_has_child relationships). Fall back to simple query.
                if "type-inference" in str(qe).lower() or "INF11" in str(qe):
                    logger.info(f"Full fetch failed for {provision_type}, using simple query: {qe}")
                    tx.close()
                    tx = typedb_client.driver.transaction(
                        typedb_client.database, TransactionType.READ
                    )
                    simple_query = _FETCH_QUERY_SIMPLE.format(
                        prov_type=provision_type, pid=provision_id
                    )
                    answer = tx.query(simple_query).resolve()
                    docs = list(answer.as_concept_documents())
                    query = simple_query  # for trace
                else:
                    raise
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
        context = f"## ENTITY DATA\n\n{entity_json}"

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
