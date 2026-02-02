"""
Pattern Detection Router

Detects covenant loopholes using TypeDB 3.x functions:
- J.Crew trapdoor vulnerability
- Yield exclusion patterns
- Collateral leakage risks
- Strong protection indicators
"""
from fastapi import APIRouter, Depends, HTTPException
from typing import List, Dict, Any

from app.services.typedb_client import typedb_client

router = APIRouter(prefix="/api/patterns", tags=["Patterns"])


@router.get("/deal/{deal_id}")
async def detect_deal_patterns(deal_id: str) -> Dict[str, Any]:
    """
    Detect all loophole patterns for a specific deal.
    
    Returns:
    - vulnerabilities: List of detected risks with severity
    - protections: List of protective provisions found
    - summary: Overall risk assessment
    """
    patterns = {
        "deal_id": deal_id,
        "vulnerabilities": [],
        "protections": [],
        "summary": {
            "vulnerability_count": 0,
            "protection_count": 0,
            "risk_level": "unknown"
        }
    }
    
    # Check J.Crew vulnerability
    jcrew = await _check_jcrew_pattern(deal_id)
    if jcrew.get("vulnerable"):
        patterns["vulnerabilities"].append({
            "pattern": "jcrew_trapdoor",
            "label": "J.Crew Trapdoor Risk",
            "severity": "high",
            "explanation": jcrew.get("explanation", ""),
            "contributing_facts": jcrew.get("facts", [])
        })
    elif jcrew.get("protected"):
        patterns["protections"].append({
            "pattern": "jcrew_blocker",
            "label": "J.Crew Blocker Present",
            "explanation": "Agreement includes J.Crew protection provisions"
        })
    
    # Check yield exclusion (MFN)
    yield_exc = await _check_yield_exclusion(deal_id)
    if yield_exc.get("detected"):
        patterns["vulnerabilities"].append({
            "pattern": "yield_exclusion",
            "label": "Yield Component Exclusion",
            "severity": "medium",
            "explanation": yield_exc.get("explanation", ""),
            "contributing_facts": yield_exc.get("facts", [])
        })
    
    # Calculate summary
    patterns["summary"]["vulnerability_count"] = len(patterns["vulnerabilities"])
    patterns["summary"]["protection_count"] = len(patterns["protections"])
    
    high_severity = any(v["severity"] == "high" for v in patterns["vulnerabilities"])
    if patterns["summary"]["vulnerability_count"] == 0:
        patterns["summary"]["risk_level"] = "low"
    elif high_severity:
        patterns["summary"]["risk_level"] = "high"
    else:
        patterns["summary"]["risk_level"] = "medium"
    
    return patterns


async def _check_jcrew_pattern(deal_id: str) -> Dict[str, Any]:
    """Check for J.Crew vulnerability pattern."""
    query = f"""
        match
            $deal isa deal, has deal_id "{deal_id}";
            (deal: $deal, provision: $rp) isa deal_has_provision;
            $rp isa rp_provision;
            $rp has unsub_designation_permitted $unsub;
            $rp has jcrew_blocker_exists $blocker;
        select $unsub, $blocker;
    """
    try:
        results = typedb_client.query_read(query)
        if not results:
            return {"vulnerable": False, "protected": False}
        
        r = results[0]
        unsub = r.get("unsub", False)
        blocker = r.get("blocker", False)
        
        if unsub and not blocker:
            return {
                "vulnerable": True,
                "protected": False,
                "explanation": "Unrestricted subsidiary designation permitted without J.Crew blocker",
                "facts": [
                    {"field": "unsub_designation_permitted", "value": True, "risk": True},
                    {"field": "jcrew_blocker_exists", "value": False, "risk": True}
                ]
            }
        elif blocker:
            return {
                "vulnerable": False,
                "protected": True,
                "explanation": "J.Crew blocker prevents unauthorized asset transfers"
            }
        
        return {"vulnerable": False, "protected": False}
        
    except Exception as e:
        return {"vulnerable": False, "protected": False, "error": str(e)}


async def _check_yield_exclusion(deal_id: str) -> Dict[str, Any]:
    """Check for yield component exclusion pattern in MFN."""
    query = f"""
        match
            $deal isa deal, has deal_id "{deal_id}";
            (deal: $deal, provision: $mfn) isa deal_has_provision;
            $mfn isa mfn_provision;
            $mfn has floor_included_in_yield $floor;
            $mfn has oid_included_in_yield $oid;
        select $floor, $oid;
    """
    try:
        results = typedb_client.query_read(query)
        if not results:
            return {"detected": False}
        
        r = results[0]
        floor = r.get("floor", True)
        oid = r.get("oid", True)
        
        excluded = []
        if not floor:
            excluded.append("LIBOR floor")
        if not oid:
            excluded.append("OID")
        
        if len(excluded) >= 2:
            return {
                "detected": True,
                "explanation": f"Multiple yield components excluded: {', '.join(excluded)}",
                "facts": [
                    {"field": "floor_included_in_yield", "value": floor},
                    {"field": "oid_included_in_yield", "value": oid}
                ]
            }
        
        return {"detected": False}
        
    except Exception:
        return {"detected": False}


@router.get("/jcrew-vulnerable")
async def get_jcrew_vulnerable_deals() -> List[Dict[str, Any]]:
    """Find all deals with J.Crew vulnerability."""
    query = """
        match
            $deal isa deal, has deal_id $did, has deal_name $name;
            (deal: $deal, provision: $rp) isa deal_has_provision;
            $rp isa rp_provision;
            $rp has unsub_designation_permitted true;
            $rp has jcrew_blocker_exists false;
        select $did, $name;
    """
    try:
        results = typedb_client.query_read(query)
        return [
            {
                "deal_id": r["did"],
                "deal_name": r["name"],
                "pattern": "jcrew_vulnerable",
                "severity": "high"
            }
            for r in results
        ]
    except Exception:
        return []


@router.get("/strong-protection")
async def get_strong_protection_deals() -> List[Dict[str, Any]]:
    """Find all deals with comprehensive J.Crew protection."""
    query = """
        match
            $deal isa deal, has deal_id $did, has deal_name $name;
            (deal: $deal, provision: $rp) isa deal_has_provision;
            $rp isa rp_provision;
            $rp has jcrew_blocker_exists true;
            $rp has blocker_binds_all_restricted_subs true;
        select $did, $name;
    """
    try:
        results = typedb_client.query_read(query)
        return [
            {
                "deal_id": r["did"],
                "deal_name": r["name"],
                "pattern": "strong_jcrew_protection",
                "severity": "positive"
            }
            for r in results
        ]
    except Exception:
        return []


@router.get("/summary")
async def get_pattern_summary() -> Dict[str, Any]:
    """
    Get pattern summary across all deals.
    
    Useful for dashboard overview.
    """
    summary = {
        "total_deals": 0,
        "deals_with_vulnerabilities": 0,
        "deals_with_strong_protection": 0,
        "patterns": {
            "jcrew_vulnerable": [],
            "strong_protection": []
        }
    }
    
    # Count total deals
    try:
        results = typedb_client.query_read("match $d isa deal; select $d;")
        summary["total_deals"] = len(results)
    except Exception:
        pass
    
    # Get vulnerable deals
    try:
        vulnerable = await get_jcrew_vulnerable_deals()
        summary["patterns"]["jcrew_vulnerable"] = vulnerable
        summary["deals_with_vulnerabilities"] = len(vulnerable)
    except Exception:
        pass
    
    # Get protected deals
    try:
        protected = await get_strong_protection_deals()
        summary["patterns"]["strong_protection"] = protected
        summary["deals_with_strong_protection"] = len(protected)
    except Exception:
        pass
    
    return summary
