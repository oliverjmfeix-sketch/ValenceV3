"""
Graph Traversal — builds entity context via single polymorphic TypeDB fetch.

All entities, all attributes, all annotations, all children in one query.
TypeDB schema is the single source of truth — no hardcoded attribute lists.
"""
import json
import logging
import time

from typedb.driver import TransactionType

from app.services.trace_collector import TraceCollector
from app.services.typedb_client import typedb_client

logger = logging.getLogger(__name__)


# ═════════════════════════════════════════════════════════════════════════════
# POLYMORPHIC FETCH QUERY
# ═════════════════════════════════════════════════════════════════════════════

_FETCH_QUERY = '''
match
    $p isa rp_provision, has provision_id "{pid}";
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
            ($e, $child) isa $child_rel;
            not {{ $child_rel sub provision_has_extracted_entity; }};
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
    ]
}};
'''


# ═════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═════════════════════════════════════════════════════════════════════════════

def get_rp_entities(deal_id: str, trace: TraceCollector = None) -> str:
    """Build entity context for Claude synthesis.

    Single polymorphic fetch: all entities, all attributes, all annotations,
    all children. TypeDB schema is the single source of truth.
    """
    if not typedb_client.driver:
        return "(TypeDB not connected)"

    provision_id = f"{deal_id}_rp"

    if trace:
        trace.provision_id = provision_id

    try:
        start = time.time()
        tx = typedb_client.driver.transaction(
            typedb_client.database, TransactionType.READ
        )
        try:
            query = _FETCH_QUERY.format(pid=provision_id)
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
            return "(No Channel 3 entities found for this provision)"

        entity_json = json.dumps(docs, indent=2, default=str)
        context = f"## ENTITY DATA\n\n{entity_json}"

        if trace:
            trace.entity_context = context
            trace.entity_context_chars = len(context)

        return context
    except Exception as e:
        logger.error(f"Polymorphic entity fetch failed: {e}")
        return "(TypeDB query failed)"
