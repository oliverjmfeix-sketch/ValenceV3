"""
Graph Traversal — builds entity context using TypeDB analytical functions.

Findings-first: computed analysis leads, supporting entity data follows.
Claude explains pre-computed findings instead of reasoning from raw data.
"""
import logging
from typing import List

from app.services.trace_collector import TraceCollector
from app.services.typedb_client import typedb_client
from app.services.graph_reader import (
    run_query,
    safe_val,
    # Entity fetchers (supporting data)
    fetch_rp_baskets,
    fetch_builder_sources,
    fetch_reallocations,
    fetch_rdp_baskets,
    fetch_jcrew_blocker,
    fetch_investment_pathways,
    fetch_unsub_designation,
    fetch_sweep_tiers,
    fetch_de_minimis,
    # Dividend capacity (already computed, used in findings)
    fetch_dividend_capacity,
)

logger = logging.getLogger(__name__)


# ═════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═════════════════════════════════════════════════════════════════════════════

def get_rp_entities(deal_id: str, trace: TraceCollector = None) -> str:
    """Build entity context for Claude synthesis.

    Section 1: Computed findings from TypeDB analytical functions
    Section 2: Supporting entity data with annotations
    """
    if not typedb_client.driver:
        return "(TypeDB not connected)"

    provision_id = f"{deal_id}_rp"

    if trace:
        trace.provision_id = provision_id

    sections = []

    # Section 1: Computed findings
    findings = _fetch_computed_findings(provision_id, trace)
    if findings:
        sections.append(findings)

    # Section 2: Supporting entity data (baskets, blocker, pathways, etc.)
    entity_data = _fetch_supporting_entities(provision_id, trace)
    if entity_data:
        sections.append(entity_data)

    if not sections:
        return "(No Channel 3 entities found for this provision)"

    context = "\n\n".join(sections)

    if trace:
        trace.entity_context = context
        trace.entity_context_chars = len(context)

    return context


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 1: COMPUTED FINDINGS
# ═════════════════════════════════════════════════════════════════════════════

def _fetch_computed_findings(provision_id: str, trace: TraceCollector = None) -> str:
    """Call TypeDB analytical functions and format as findings."""
    findings = []

    # 1. Dividend Capacity (existing function, imported from graph_reader)
    cap_lines = fetch_dividend_capacity(provision_id, trace=trace)
    if cap_lines:
        findings.append("\n".join(cap_lines))

    # 2. Blocker Gap Analysis
    gap_lines = _fetch_blocker_gap_findings(provision_id, trace)
    if gap_lines:
        findings.append("\n".join(gap_lines))

    # 3. Exception Swallow Analysis
    exception_lines = _fetch_exception_findings(provision_id, trace)
    if exception_lines:
        findings.append("\n".join(exception_lines))

    # 4. Unsub Distribution Analysis
    dist_lines = _fetch_distribution_findings(provision_id, trace)
    if dist_lines:
        findings.append("\n".join(dist_lines))

    # 5. Pathway Analysis
    pathway_lines = _fetch_pathway_findings(provision_id, trace)
    if pathway_lines:
        findings.append("\n".join(pathway_lines))

    if not findings:
        return ""

    return "## COMPUTED FINDINGS\n\n" + "\n\n".join(findings)


def _fetch_blocker_gap_findings(provision_id: str, trace: TraceCollector = None) -> List[str]:
    """Call blocker_binding_gap_evidence and format results."""
    try:
        query = f'''
            match
                let $gc, $gn in blocker_binding_gap_evidence("{provision_id}");
            select $gc, $gn;
        '''
        rows = run_query(query, trace=trace, trace_name="blocker_binding_gap")

        if not rows:
            return ["### Blocker Coverage Gaps", "  No gaps detected — blocker covers all checked areas."]

        lines = ["### Blocker Coverage Gaps"]
        gaps_by_cat = {}
        for row in rows:
            cat = safe_val(row, "gc") or "other"
            detail = safe_val(row, "gn") or "unknown"
            gaps_by_cat.setdefault(cat, []).append(detail)

        for cat, details in sorted(gaps_by_cat.items()):
            lines.append(f"  {cat}:")
            for d in details:
                lines.append(f"    - {d} = false")

        return lines
    except Exception as e:
        logger.warning(f"blocker_binding_gap_evidence failed: {e}")
        return []


def _fetch_exception_findings(provision_id: str, trace: TraceCollector = None) -> List[str]:
    """Call blocker_exception_swallow_evidence and format results."""
    try:
        query = f'''
            match
                let $ename in blocker_exception_swallow_evidence("{provision_id}");
            select $ename;
        '''
        rows = run_query(query, trace=trace, trace_name="blocker_exception_swallow")

        if not rows:
            return ["### Blocker Exception Analysis", "  No exceptions found that could weaken the blocker."]

        lines = ["### Blocker Exception Analysis"]
        lines.append(f"  {len(rows)} exception(s) that may weaken blocker:")
        for row in rows:
            ename = safe_val(row, "ename") or "unknown"
            lines.append(f"  - {ename}")

        return lines
    except Exception as e:
        logger.warning(f"blocker_exception_swallow_evidence failed: {e}")
        return []


def _fetch_distribution_findings(provision_id: str, trace: TraceCollector = None) -> List[str]:
    """Call unsub_distribution_evidence and format results."""
    try:
        query = f'''
            match
                let $an, $val in unsub_distribution_evidence("{provision_id}");
            select $an, $val;
        '''
        rows = run_query(query, trace=trace, trace_name="unsub_distribution")

        if not rows:
            return ["### Unsub Distribution Analysis", "  No unsub distribution basket found."]

        lines = ["### Unsub Distribution Analysis"]
        for row in rows:
            attr_name = safe_val(row, "an") or "unknown"
            attr_value = safe_val(row, "val") or "unknown"
            # Values come back as strings "true"/"false" from the function
            display = "YES" if str(attr_value).lower() == "true" else "NO" if str(attr_value).lower() == "false" else str(attr_value)
            lines.append(f"  {attr_name}: {display}")

        return lines
    except Exception as e:
        logger.warning(f"unsub_distribution_evidence failed: {e}")
        return []


def _fetch_pathway_findings(provision_id: str, trace: TraceCollector = None) -> List[str]:
    """Call pathway_chain_summary and format results."""
    try:
        query = f'''
            match
                let $src, $tgt, $unc in pathway_chain_summary("{provision_id}");
            select $src, $tgt, $unc;
        '''
        rows = run_query(query, trace=trace, trace_name="pathway_chain_summary")

        if not rows:
            return ["### Investment Pathways", "  No pathways found."]

        lines = ["### Investment Pathways"]
        hops = []
        for row in rows:
            src = safe_val(row, "src")
            tgt = safe_val(row, "tgt")
            unc = safe_val(row, "unc")

            l = f"  {src} \u2192 {tgt}"
            if unc:
                l += " (UNCAPPED)"
            lines.append(l)

            if src and tgt:
                hops.append((str(src), str(tgt)))

        # Stitch multi-hop chains
        chains = _stitch_chains(hops)
        if chains:
            lines.append("  Complete chains:")
            for chain in chains:
                arrow = ' \u2192 '
            lines.append(f"    {arrow.join(chain)}")

        return lines
    except Exception as e:
        logger.warning(f"pathway_chain_summary failed: {e}")
        return []


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 2: SUPPORTING ENTITY DATA
# ═════════════════════════════════════════════════════════════════════════════

def _fetch_supporting_entities(provision_id: str, trace: TraceCollector = None) -> str:
    """Fetch supporting entity data with annotations for citations.

    Excludes dividend capacity (already in findings, same format).
    Keeps blocker/pathways because findings report analysis results
    while supporting data has raw attributes + annotations for disambiguation.
    """
    sections = []

    fetchers = [
        fetch_rp_baskets,
        fetch_builder_sources,
        fetch_reallocations,
        fetch_rdp_baskets,
        fetch_jcrew_blocker,
        fetch_investment_pathways,
        fetch_unsub_designation,
        fetch_sweep_tiers,
        fetch_de_minimis,
    ]

    for fetcher in fetchers:
        try:
            lines = fetcher(provision_id, trace=trace)
            if lines:
                sections.append("\n".join(lines))
        except Exception as e:
            logger.warning(f"{fetcher.__name__} failed: {e}")

    if not sections:
        return ""

    return "## SUPPORTING ENTITY DATA\n\n" + "\n\n".join(sections)


# ═════════════════════════════════════════════════════════════════════════════
# UTILITIES
# ═════════════════════════════════════════════════════════════════════════════

def _stitch_chains(hops: list) -> list:
    """Stitch pathway hops into complete chains.

    Input: [("loan_party", "non_guarantor_rs"), ("non_guarantor_rs", "unrestricted_sub")]
    Output: [["loan_party", "non_guarantor_rs", "unrestricted_sub"]]
    """
    if not hops:
        return []

    target_types = {tgt for _, tgt in hops}
    start_types = {src for src, _ in hops if src not in target_types}

    # If no clear start, use all source types
    if not start_types:
        start_types = {src for src, _ in hops}

    chains = []
    for start in start_types:
        _walk_chain(start, hops, [start], chains)

    # Only return multi-hop chains (single hops are already displayed above)
    return [c for c in chains if len(c) > 2]


def _walk_chain(current, hops, path, chains):
    """Recursively walk hops to build chains."""
    next_hops = [(s, t) for s, t in hops if s == current and t not in path]
    if not next_hops:
        if len(path) > 1:
            chains.append(path[:])
        return
    for _, target in next_hops:
        path.append(target)
        _walk_chain(target, hops, path, chains)
        path.pop()
