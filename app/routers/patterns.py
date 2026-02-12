"""
Pattern Detection Router — J.Crew vulnerability patterns via TypeDB functions.

Functions are defined in jcrew_functions.tql and live in the TypeDB schema.
They compute patterns on the fly from extracted data — no stale flags.
"""
import logging
from fastapi import APIRouter, HTTPException
from typing import Dict, Any, List

from app.services.typedb_client import typedb_client
from app.config import settings
from typedb.driver import TransactionType

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/patterns", tags=["Patterns"])

# Pattern function names → categories + descriptions.
# Logic lives in TypeDB (jcrew_functions.tql); this is display metadata only.
PATTERN_FUNCTIONS = {
    # Vulnerabilities (10)
    "pattern_no_blocker": {
        "category": "vulnerability",
        "description": "No J.Crew blocker exists despite unsub designation being permitted",
    },
    "pattern_chain_pathway_open": {
        "category": "vulnerability",
        "description": "Investment chain from LP to Unsub is open",
    },
    "pattern_blocker_scope_gap": {
        "category": "vulnerability",
        "description": "Blocker does not apply at all times",
    },
    "pattern_blocker_timing_gap": {
        "category": "vulnerability",
        "description": "Blocker only applies at designation, not ongoing",
    },
    "pattern_licensing_gap": {
        "category": "vulnerability",
        "description": "Transfer definition excludes exclusive licensing",
    },
    "pattern_amendment_vulnerable": {
        "category": "vulnerability",
        "description": "Blocker can be amended by required lenders",
    },
    "pattern_automatic_lien_release": {
        "category": "vulnerability",
        "description": "Liens auto-release on permitted transfers",
    },
    "pattern_no_unsub_cap": {
        "category": "vulnerability",
        "description": "Unsub designation permitted with no cap",
    },
    "pattern_basket_fungibility": {
        "category": "vulnerability",
        "description": "Investment baskets can rebuild",
    },
    "pattern_basket_stacking": {
        "category": "vulnerability",
        "description": "Multiple baskets can stack",
    },
    # Interactions (5)
    "interaction_ip_definition_narrower": {
        "category": "interaction",
        "description": "IP definition excludes trade secrets despite blocker",
    },
    "interaction_transfer_definition_narrower": {
        "category": "interaction",
        "description": "Transfer definition excludes licensing despite blocker",
    },
    "interaction_material_definition_undermines": {
        "category": "interaction",
        "description": "Material definition is subjective despite blocker",
    },
    "interaction_blocker_scope_misses_chain": {
        "category": "interaction",
        "description": "Chain pathway open AND blocker only at designation",
    },
    "interaction_blocker_timing_mismatches": {
        "category": "interaction",
        "description": "Blocker timing mismatches designation conditions",
    },
    # Protections (13)
    "protection_blocker_covers_ownership": {
        "category": "protection",
        "description": "Blocker covers ownership and applies at all times",
    },
    "protection_blocker_covers_all_licensing": {
        "category": "protection",
        "description": "Transfer definition includes all licensing",
    },
    "protection_blocker_covers_restricted_subs": {
        "category": "protection",
        "description": "Blocker binds all restricted subs",
    },
    "protection_blocker_applies_at_all_times": {
        "category": "protection",
        "description": "Blocker protection is ongoing",
    },
    "protection_blocker_is_sacred_right": {
        "category": "protection",
        "description": "Blocker amendment requires all-lender consent",
    },
    "protection_ip_definition_comprehensive": {
        "category": "protection",
        "description": "IP definition includes trade secrets and know-how",
    },
    "protection_transfer_definition_comprehensive": {
        "category": "protection",
        "description": "Transfer definition includes all licensing",
    },
    "protection_material_definition_objective": {
        "category": "protection",
        "description": "Material definition uses objective criteria",
    },
    "protection_unsub_has_hard_cap": {
        "category": "protection",
        "description": "Unsub designation has hard dollar cap",
    },
    "protection_unsub_has_ebitda_cap": {
        "category": "protection",
        "description": "Unsub designation has EBITDA cap",
    },
    "protection_dedicated_basket_required": {
        "category": "protection",
        "description": "No basket stacking or rebuilding",
    },
    "protection_material_assets_covered": {
        "category": "protection",
        "description": "Material assets objectively defined and blocker exists",
    },
    "protection_lien_release_requires_consent": {
        "category": "protection",
        "description": "IP lien release requires lender consent",
    },
}


def _evaluate_patterns(deal_ref: str) -> Dict[str, bool]:
    """Call all 28 TypeDB functions for a deal and return results."""
    if not typedb_client.driver:
        raise HTTPException(503, "Database not connected")

    results = {}
    tx = typedb_client.driver.transaction(
        settings.typedb_database, TransactionType.READ
    )
    try:
        for pattern_name in PATTERN_FUNCTIONS:
            fun_name = f"has_{pattern_name}"
            try:
                query = f'match true == {fun_name}("{deal_ref}"); select;'
                result = tx.query(query).resolve()
                rows = list(result.as_concept_rows())
                results[pattern_name] = len(rows) > 0
            except Exception:
                results[pattern_name] = False
        return results
    finally:
        tx.close()


@router.get("/deal/{deal_id}")
async def detect_deal_patterns(deal_id: str) -> Dict[str, Any]:
    """Detect all J.Crew loophole patterns for a deal using TypeDB functions."""
    results = _evaluate_patterns(deal_id)

    vulnerabilities = [
        {"pattern": k, "description": PATTERN_FUNCTIONS[k]["description"]}
        for k, detected in results.items()
        if detected and PATTERN_FUNCTIONS[k]["category"] == "vulnerability"
    ]
    interactions = [
        {"pattern": k, "description": PATTERN_FUNCTIONS[k]["description"]}
        for k, detected in results.items()
        if detected and PATTERN_FUNCTIONS[k]["category"] == "interaction"
    ]
    protections = [
        {"pattern": k, "description": PATTERN_FUNCTIONS[k]["description"]}
        for k, detected in results.items()
        if detected and PATTERN_FUNCTIONS[k]["category"] == "protection"
    ]

    return {
        "deal_id": deal_id,
        "vulnerabilities": vulnerabilities,
        "interactions": interactions,
        "protections": protections,
        "summary": {
            "vulnerability_count": len(vulnerabilities),
            "interaction_count": len(interactions),
            "protection_count": len(protections),
            "risk_level": (
                "high" if len(vulnerabilities) >= 3
                else "medium" if len(vulnerabilities) >= 1
                else "low"
            ),
        },
    }


@router.get("/jcrew-vulnerable")
async def get_jcrew_vulnerable() -> List[Dict[str, Any]]:
    """Find all deals with at least one J.Crew vulnerability."""
    if not typedb_client.driver:
        raise HTTPException(503, "Database not connected")

    tx = typedb_client.driver.transaction(
        settings.typedb_database, TransactionType.READ
    )
    try:
        result = tx.query("""
            match
                $a isa jcrew_provision_analysis, has deal_ref $did;
            select $did;
        """).resolve()

        deal_refs = []
        for row in result.as_concept_rows():
            deal_refs.append(row.get("did").as_attribute().get_value())
    finally:
        tx.close()

    vulnerable = []
    for ref in deal_refs:
        results = _evaluate_patterns(ref)
        vulns = [
            k for k, detected in results.items()
            if detected and PATTERN_FUNCTIONS[k]["category"] == "vulnerability"
        ]
        if vulns:
            vulnerable.append({
                "deal_id": ref,
                "vulnerability_count": len(vulns),
                "patterns": vulns,
            })

    return vulnerable


@router.get("/summary")
async def pattern_summary() -> Dict[str, Any]:
    """Pattern summary across all deals."""
    if not typedb_client.driver:
        return {"total_deals": 0, "status": "database_not_connected"}

    try:
        tx = typedb_client.driver.transaction(
            settings.typedb_database, TransactionType.READ
        )
        try:
            result = tx.query("match $d isa deal; select $d;").resolve()
            total = len(list(result.as_concept_rows()))

            jcrew_result = tx.query("""
                match $a isa jcrew_provision_analysis; select $a;
            """).resolve()
            jcrew_count = len(list(jcrew_result.as_concept_rows()))

            return {
                "total_deals": total,
                "jcrew_analyzed_count": jcrew_count,
                "status": "ok",
            }
        finally:
            tx.close()
    except Exception as e:
        return {"total_deals": 0, "error": str(e), "status": "error"}
