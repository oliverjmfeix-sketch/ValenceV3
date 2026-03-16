"""
Graph Reader — reads Channel 3 typed entities from TypeDB for a provision.

Returns formatted text suitable for Claude synthesis prompts.
"""
import logging
import re as _re
import time as _time
from typing import Dict, List, Any, Optional

from typedb.driver import TransactionType

from app.config import settings
from app.services.typedb_client import typedb_client
from app.services.trace_collector import TraceCollector

logger = logging.getLogger(__name__)

# ── Lazy-loaded question text cache ─────────────────────────────────────────
_question_texts: Optional[dict] = None

# ── Lazy-loaded attribute annotation cache (replaces attribute_glossary.py) ──
_annotation_cache: Optional[Dict[str, Dict[str, str]]] = None
_annotation_cache_time: float = 0
_ANNOTATION_CACHE_TTL = 600  # 10 minutes


def _get_question_texts() -> dict:
    """Return question_id → question_text mapping, loading once from TypeDB."""
    global _question_texts
    if _question_texts is None:
        _question_texts = _load_question_texts()
    return _question_texts


def _load_question_texts() -> dict:
    """Load all ontology question texts from TypeDB."""
    tx = typedb_client.driver.transaction(settings.typedb_database, TransactionType.READ)
    try:
        query = """
            match $q isa ontology_question,
                has question_id $qid,
                has question_text $qt;
            select $qid, $qt;
        """
        results = list(tx.query(query).resolve().as_concept_rows())
        texts = {}
        for row in results:
            qid = row.get("qid").as_attribute().get_value()
            qt = row.get("qt").as_attribute().get_value()
            texts[qid] = qt
        logger.info(f"Loaded {len(texts)} question texts for entity annotation")
        return texts
    except Exception as e:
        logger.warning(f"Failed to load question texts: {e}")
        return {}
    finally:
        if tx.is_open():
            tx.close()


def _get_annotation_map() -> Dict[str, Dict[str, str]]:
    """Load attribute → question_id mapping from TypeDB.

    Returns dict[entity_type][attribute_name] → question_id.
    Replaces ATTRIBUTE_GLOSSARY and REALLOCATION_ANNOTATIONS from attribute_glossary.py.
    """
    global _annotation_cache, _annotation_cache_time
    now = _time.time()
    if _annotation_cache is not None and (now - _annotation_cache_time) < _ANNOTATION_CACHE_TTL:
        return _annotation_cache

    query = """
        match
            (question: $q) isa question_annotates_attribute,
                has target_entity_type $et,
                has target_attribute_name $an;
            $q has question_id $qid;
        select $qid, $et, $an;
    """
    result_map: Dict[str, Dict[str, str]] = {}
    tx = typedb_client.driver.transaction(settings.typedb_database, TransactionType.READ)
    try:
        results = list(tx.query(query).resolve().as_concept_rows())
        for row in results:
            qid = row.get("qid").as_attribute().get_value()
            et = row.get("et").as_attribute().get_value()
            an = row.get("an").as_attribute().get_value()
            if qid and et and an:
                result_map.setdefault(et, {})[an] = qid
        logger.info(f"Loaded {sum(len(v) for v in result_map.values())} attribute annotations from TypeDB")
    except Exception as e:
        logger.error(f"Failed to load attribute annotations from TypeDB: {e}")
        result_map = {}
    finally:
        if tx.is_open():
            tx.close()

    _annotation_cache = result_map
    _annotation_cache_time = now
    return result_map


def validate_annotations() -> bool:
    """Validate that question_annotates_attribute data in TypeDB is consistent."""
    annotations = _get_annotation_map()
    question_texts = _get_question_texts()

    total = sum(len(v) for v in annotations.values())
    missing = []
    for et, attrs in annotations.items():
        for attr_name, qid in attrs.items():
            if qid not in question_texts:
                missing.append(f"  {et}.{attr_name} -> {qid}")

    if missing:
        logger.warning(f"Annotation references {len(missing)} missing question_ids:\n" + "\n".join(missing))
    else:
        logger.info(f"Annotation validation passed: {total} annotations, all question_ids found")

    return len(missing) == 0


def fmt_dollar(val) -> str:
    """Format a numeric value as $X,XXX,XXX."""
    if val is None:
        return ""
    try:
        return f"${val:,.0f}"
    except (TypeError, ValueError):
        return str(val)


def fmt_pct(val) -> str:
    """Format a numeric value as X%.

    Values stored as decimals (e.g., 1.0 = 100%, 0.5 = 50%) are converted.
    Values > 1 are assumed to already be percentages.
    """
    if val is None:
        return ""
    try:
        v = float(val)
        if v <= 1.0:
            return f"{v * 100:.0f}%"
        return f"{v:.1f}%"
    except (TypeError, ValueError):
        return str(val)


def safe_val(row, key: str):
    """Extract attribute or value from a TypeDB row, returning None on failure.

    Handles both has-bound variables (Attribute) and let-bound variables (Value).
    """
    try:
        concept = row.get(key)
        if concept is None:
            return None
        try:
            return concept.as_attribute().get_value()
        except Exception:
            return concept.as_value().get()
    except Exception:
        return None


def safe_type(row, key: str) -> Optional[str]:
    """Get the type label of an entity variable."""
    try:
        concept = row.get(key)
        if concept is None:
            return None
        return concept.as_entity().get_type().get_label()
    except Exception:
        return None


def line(label: str, value, formatter=None, *,
          entity_type: str = None, attr_key: str = None) -> Optional[str]:
    """Return a formatted line if value is not None, else None.

    If entity_type and attr_key are provided, appends the source ontology
    question text as an annotation line.
    """
    if value is None:
        return None
    if formatter:
        formatted = formatter(value)
        line = f"  {label}: {formatted}"
    elif isinstance(value, bool):
        line = f"  {label}: {'true' if value else 'false'}"
    else:
        line = f"  {label}: {value}"

    # Append question annotation if available
    if entity_type and attr_key:
        qid = _get_annotation_map().get(entity_type, {}).get(attr_key)
        if qid:
            qt = _get_question_texts().get(qid)
            if qt:
                line += f'\n    \u2192 "{qt}"'

    return line


def add_lines(lines: list, items: list):
    """Append non-None items to lines."""
    for item in items:
        if item is not None:
            lines.append(item)


def _get_variable_names(query: str) -> List[str]:
    """Extract $variable names from the select clause of a TQL query."""
    select_match = _re.search(r'select\s+(.+?);', query, _re.IGNORECASE | _re.DOTALL)
    if select_match:
        return _re.findall(r'\$(\w+)', select_match.group(1))
    return []


def run_query(query: str, trace: TraceCollector = None, trace_name: str = "") -> list:
    """Execute a read query and return rows. Optionally trace."""
    start = _time.time()
    tx = typedb_client.driver.transaction(settings.typedb_database, TransactionType.READ)
    try:
        result = list(tx.query(query).resolve().as_concept_rows())
        duration_ms = (_time.time() - start) * 1000

        if trace and trace_name:
            sample = []
            for row in result[:5]:
                row_dict = {}
                for var_name in _get_variable_names(query):
                    val = safe_val(row, var_name)
                    if val is not None:
                        row_dict[var_name] = val
                sample.append(row_dict)
            trace.add_query(trace_name, query, len(result), duration_ms, sample)

        return result
    except Exception as e:
        logger.debug(f"Query failed: {e}")
        if trace and trace_name:
            trace.add_query(trace_name, query, 0, (_time.time() - start) * 1000)
        return []
    finally:
        tx.close()


def get_rp_entities(deal_id: str, trace: TraceCollector = None) -> str:
    """
    Fetch ALL Channel 3 entities for an RP provision and format as labeled text.

    Queries:
      - RP baskets (7 subtypes) via provision_has_basket
      - Builder sources via basket_has_source
      - Reallocation edges via provision_has_reallocation + basket_reallocates_to
      - RDP baskets (5 subtypes) via provision_has_rdp_basket
      - J.Crew blocker via provision_has_blocker + blocker exceptions
      - Investment pathways via provision_has_pathway
      - Unsub designation via provision_has_unsub
      - Sweep tiers via provision_has_sweep_tier
      - De minimis thresholds via provision_has_de_minimis
    """
    if not typedb_client.driver:
        return "(TypeDB not connected)"

    provision_id = f"{deal_id}_rp"

    if trace:
        trace.provision_id = provision_id

    sections = []

    # ── RP Baskets ────────────────────────────────────────────────────
    basket_lines = fetch_rp_baskets(provision_id, trace=trace)
    if basket_lines:
        sections.append("\n".join(basket_lines))

    # ── Builder Sources ───────────────────────────────────────────────
    source_lines = fetch_builder_sources(provision_id, trace=trace)
    if source_lines:
        sections.append("\n".join(source_lines))

    # ── Reallocations ─────────────────────────────────────────────────
    realloc_lines = fetch_reallocations(provision_id, trace=trace)
    if realloc_lines:
        sections.append("\n".join(realloc_lines))

    # ── RDP Baskets ───────────────────────────────────────────────────
    rdp_lines = fetch_rdp_baskets(provision_id, trace=trace)
    if rdp_lines:
        sections.append("\n".join(rdp_lines))

    # ── J.Crew Blocker ────────────────────────────────────────────────
    blocker_lines = fetch_jcrew_blocker(provision_id, trace=trace)
    if blocker_lines:
        sections.append("\n".join(blocker_lines))

    # ── Investment Pathways ───────────────────────────────────────────
    pathway_lines = fetch_investment_pathways(provision_id, trace=trace)
    if pathway_lines:
        sections.append("\n".join(pathway_lines))

    # ── Unsub Designation ─────────────────────────────────────────────
    unsub_lines = fetch_unsub_designation(provision_id, trace=trace)
    if unsub_lines:
        sections.append("\n".join(unsub_lines))

    # ── Sweep Tiers ───────────────────────────────────────────────────
    sweep_lines = fetch_sweep_tiers(provision_id, trace=trace)
    if sweep_lines:
        sections.append("\n".join(sweep_lines))

    # ── De Minimis Thresholds ─────────────────────────────────────────
    dm_lines = fetch_de_minimis(provision_id, trace=trace)
    if dm_lines:
        sections.append("\n".join(dm_lines))

    # ── Dividend Capacity Summary (TypeDB function, prepended) ───────
    cap_lines = fetch_dividend_capacity(provision_id, trace=trace)
    if cap_lines:
        sections.insert(0, "\n".join(cap_lines))

    if not sections:
        return "(No Channel 3 entities found for this provision)"

    entity_context = "\n\n".join(sections)

    if trace:
        trace.entity_context = entity_context
        trace.entity_context_chars = len(entity_context)

    return entity_context


# ═════════════════════════════════════════════════════════════════════════════
# INDIVIDUAL ENTITY FETCHERS
# ═════════════════════════════════════════════════════════════════════════════

def fetch_dividend_capacity(provision_id: str, trace: TraceCollector = None) -> List[str]:
    """Call TypeDB dividend_capacity_components function.

    Traversal logic lives in TypeDB. Python only formats the output.
    """
    query = f'''
        match
            let $dn, $amt in dividend_capacity_components("{provision_id}");
        select $dn, $amt;
    '''
    rows = run_query(query, trace=trace, trace_name="dividend_capacity_components")
    if not rows:
        return []

    components = []
    total = 0.0
    for row in rows:
        dn = safe_val(row, "dn")
        amt = safe_val(row, "amt")
        if dn and amt and amt > 0:
            components.append((dn, amt))
            total += amt

    if not components:
        return []

    if trace:
        trace.capacity_total = total
        trace.capacity_components = [
            {"name": name, "amount": amt} for name, amt in components
        ]

    lines = [
        "## Dividend Capacity Summary",
        f"  Total Fixed Floor: {fmt_dollar(total)}",
        '    \u2192 "What is the total quantifiable fixed-floor dividend capacity '
        'from all independently-sectioned baskets plus reallocatable capacity?"',
        "  Components (independently-sectioned baskets \u2014 additive):",
    ]
    for name, amt in sorted(components, key=lambda x: -x[1]):
        lines.append(f"    {name}: {fmt_dollar(amt)}")
    lines.append(
        "  Methodology: Each component is from a separately-defined covenant "
        "section. Reallocation capacity is additive \u2014 the source basket\u2019s "
        "amount adds to the receiving basket\u2019s dividend capacity on a "
        "dollar-for-dollar basis."
    )
    return lines


def fetch_rp_baskets(provision_id: str, trace: TraceCollector = None) -> List[str]:
    """Fetch all RP basket subtypes using per-subtype queries to avoid type inference issues."""
    all_baskets = []

    # Query 1: Builder basket
    q_builder = f'''
        match
            $p isa rp_provision, has provision_id "{provision_id}";
            (provision: $p, basket: $b) isa provision_has_basket;
            $b isa builder_basket, has basket_id $bid;
            try {{ $b has section_reference $sec; }};
            try {{ $b has source_page $pg; }};
            try {{ $b has start_date_language $sdl; }};
            try {{ $b has uses_greatest_of_tests $ugot; }};
            try {{ $b has default_condition $dc; }};
        select $b, $bid, $sec, $pg, $sdl, $ugot, $dc;
    '''
    for row in run_query(q_builder, trace=trace, trace_name="rp_basket_builder"):
        lines = ["### Builder Basket"]
        sec, pg = safe_val(row, "sec"), safe_val(row, "pg")
        if sec or pg is not None:
            lines.append(f"  {', '.join(filter(None, [f'Section: {sec}' if sec else None, f'Page: {pg}' if pg is not None else None]))}")
        add_lines(lines, [
            line("Uses Greatest Of Tests", safe_val(row, "ugot"),
                  entity_type="builder_basket", attr_key="uses_greatest_of_tests"),
            line("Start Date Language", safe_val(row, "sdl"),
                  entity_type="builder_basket", attr_key="start_date_language"),
            line("Default Condition", safe_val(row, "dc")),
        ])
        all_baskets.append(lines)

    # Query 2: Ratio basket
    q_ratio = f'''
        match
            $p isa rp_provision, has provision_id "{provision_id}";
            (provision: $p, basket: $b) isa provision_has_basket;
            $b isa ratio_basket, has basket_id $bid;
            try {{ $b has section_reference $sec; }};
            try {{ $b has source_page $pg; }};
            try {{ $b has ratio_threshold $rt; }};
            try {{ $b has ratio_type $rty; }};
            try {{ $b has is_unlimited_if_met $ium; }};
            try {{ $b has has_no_worse_test $nwt; }};
            try {{ $b has no_worse_threshold $nwthr; }};
            try {{ $b has no_worse_is_uncapped $nwu; }};
            try {{ $b has test_date_type $tdt; }};
            try {{ $b has lct_treatment_available $lct; }};
            try {{ $b has pro_forma_basis $pfb; }};
            try {{ $b has default_condition $dc; }};
        select $b, $bid, $sec, $pg, $rt, $rty, $ium, $nwt, $nwthr, $nwu, $tdt, $lct, $pfb, $dc;
    '''
    for row in run_query(q_ratio, trace=trace, trace_name="rp_basket_ratio"):
        lines = ["### Ratio Basket"]
        sec, pg = safe_val(row, "sec"), safe_val(row, "pg")
        if sec or pg is not None:
            lines.append(f"  {', '.join(filter(None, [f'Section: {sec}' if sec else None, f'Page: {pg}' if pg is not None else None]))}")
        add_lines(lines, [
            line("Ratio Threshold", safe_val(row, "rt"),
                  entity_type="ratio_basket", attr_key="ratio_threshold"),
            line("Ratio Type", safe_val(row, "rty")),
            line("Is Unlimited If Met", safe_val(row, "ium"),
                  entity_type="ratio_basket", attr_key="is_unlimited_if_met"),
            line("Has No Worse Test", safe_val(row, "nwt"),
                  entity_type="ratio_basket", attr_key="has_no_worse_test"),
            line("No Worse Threshold",
                  "uncapped" if safe_val(row, "nwu") else safe_val(row, "nwthr"),
                  entity_type="ratio_basket", attr_key="no_worse_is_uncapped"),
            line("Test Date Type", safe_val(row, "tdt")),
            line("LCT Treatment Available", safe_val(row, "lct")),
            line("Pro Forma Basis", safe_val(row, "pfb")),
            line("Default Condition", safe_val(row, "dc")),
        ])
        all_baskets.append(lines)

    # Query 3: General RP basket
    q_general = f'''
        match
            $p isa rp_provision, has provision_id "{provision_id}";
            (provision: $p, basket: $b) isa provision_has_basket;
            $b isa general_rp_basket, has basket_id $bid;
            try {{ $b has section_reference $sec; }};
            try {{ $b has source_page $pg; }};
            try {{ $b has basket_amount_usd $bau; }};
            try {{ $b has basket_grower_pct $bgp; }};
            try {{ $b has is_per_annum $ipa; }};
            try {{ $b has default_condition $dc; }};
        select $b, $bid, $sec, $pg, $bau, $bgp, $ipa, $dc;
    '''
    for row in run_query(q_general, trace=trace, trace_name="rp_basket_general"):
        lines = ["### General Rp Basket"]
        sec, pg = safe_val(row, "sec"), safe_val(row, "pg")
        if sec or pg is not None:
            lines.append(f"  {', '.join(filter(None, [f'Section: {sec}' if sec else None, f'Page: {pg}' if pg is not None else None]))}")
        add_lines(lines, [
            line("Basket Amount", safe_val(row, "bau"), fmt_dollar,
                  entity_type="general_rp_basket", attr_key="basket_amount_usd"),
            line("Grower Pct", safe_val(row, "bgp"), fmt_pct,
                  entity_type="general_rp_basket", attr_key="basket_grower_pct"),
            line("Is Per Annum", safe_val(row, "ipa")),
            line("Default Condition", safe_val(row, "dc")),
        ])
        all_baskets.append(lines)

    # Query 4: Management equity basket
    q_mgmt = f'''
        match
            $p isa rp_provision, has provision_id "{provision_id}";
            (provision: $p, basket: $b) isa provision_has_basket;
            $b isa management_equity_basket, has basket_id $bid;
            try {{ $b has section_reference $sec; }};
            try {{ $b has source_page $pg; }};
            try {{ $b has annual_cap_usd $acu; }};
            try {{ $b has annual_cap_pct_ebitda $acpe; }};
            try {{ $b has cap_uses_greater_of $cugo; }};
            try {{ $b has carryforward_permitted $cfp; }};
            try {{ $b has carryforward_max_years $cfmy; }};
            try {{ $b has eligible_person_scope $eps; }};
            try {{ $b has default_condition $dc; }};
        select $b, $bid, $sec, $pg, $acu, $acpe, $cugo, $cfp, $cfmy, $eps, $dc;
    '''
    for row in run_query(q_mgmt, trace=trace, trace_name="rp_basket_management"):
        lines = ["### Management Equity Basket"]
        sec, pg = safe_val(row, "sec"), safe_val(row, "pg")
        if sec or pg is not None:
            lines.append(f"  {', '.join(filter(None, [f'Section: {sec}' if sec else None, f'Page: {pg}' if pg is not None else None]))}")
        add_lines(lines, [
            line("Annual Cap", safe_val(row, "acu"), fmt_dollar,
                  entity_type="management_equity_basket", attr_key="annual_cap_usd"),
            line("Annual Cap Pct EBITDA", safe_val(row, "acpe"), fmt_pct,
                  entity_type="management_equity_basket", attr_key="annual_cap_pct_ebitda"),
            line("Cap Uses Greater Of", safe_val(row, "cugo"),
                  entity_type="management_equity_basket", attr_key="cap_uses_greater_of"),
            line("Carryforward Permitted", safe_val(row, "cfp"),
                  entity_type="management_equity_basket", attr_key="carryforward_permitted"),
            line("Carryforward Max Years", safe_val(row, "cfmy")),
            line("Eligible Person Scope", safe_val(row, "eps")),
            line("Default Condition", safe_val(row, "dc")),
        ])
        all_baskets.append(lines)

    # Query 5: Tax distribution basket
    q_tax = f'''
        match
            $p isa rp_provision, has provision_id "{provision_id}";
            (provision: $p, basket: $b) isa provision_has_basket;
            $b isa tax_distribution_basket, has basket_id $bid;
            try {{ $b has section_reference $sec; }};
            try {{ $b has source_page $pg; }};
            try {{ $b has standalone_taxpayer_limit $stl; }};
            try {{ $b has hypothetical_tax_rate $htr; }};
            try {{ $b has tax_sharing_permitted $tsp; }};
            try {{ $b has estimated_taxes_permitted $etp; }};
            try {{ $b has default_condition $dc; }};
        select $b, $bid, $sec, $pg, $stl, $htr, $tsp, $etp, $dc;
    '''
    for row in run_query(q_tax, trace=trace, trace_name="rp_basket_tax"):
        lines = ["### Tax Distribution Basket"]
        sec, pg = safe_val(row, "sec"), safe_val(row, "pg")
        if sec or pg is not None:
            lines.append(f"  {', '.join(filter(None, [f'Section: {sec}' if sec else None, f'Page: {pg}' if pg is not None else None]))}")
        add_lines(lines, [
            line("Standalone Taxpayer Limit", safe_val(row, "stl")),
            line("Hypothetical Tax Rate", safe_val(row, "htr")),
            line("Tax Sharing Permitted", safe_val(row, "tsp")),
            line("Estimated Taxes Permitted", safe_val(row, "etp")),
            line("Default Condition", safe_val(row, "dc")),
        ])
        all_baskets.append(lines)

    # Query 6: Holdco overhead basket
    q_holdco = f'''
        match
            $p isa rp_provision, has provision_id "{provision_id}";
            (provision: $p, basket: $b) isa provision_has_basket;
            $b isa holdco_overhead_basket, has basket_id $bid;
            try {{ $b has section_reference $sec; }};
            try {{ $b has source_page $pg; }};
            try {{ $b has covers_management_fees $cmf; }};
            try {{ $b has covers_admin_expenses $cae; }};
            try {{ $b has covers_franchise_taxes $cft; }};
            try {{ $b has management_fee_recipient_scope $mfrs; }};
            try {{ $b has requires_arms_length $ral; }};
            try {{ $b has requires_board_approval $rba; }};
            try {{ $b has default_condition $dc; }};
        select $b, $bid, $sec, $pg, $cmf, $cae, $cft, $mfrs, $ral, $rba, $dc;
    '''
    for row in run_query(q_holdco, trace=trace, trace_name="rp_basket_holdco"):
        lines = ["### Holdco Overhead Basket"]
        sec, pg = safe_val(row, "sec"), safe_val(row, "pg")
        if sec or pg is not None:
            lines.append(f"  {', '.join(filter(None, [f'Section: {sec}' if sec else None, f'Page: {pg}' if pg is not None else None]))}")
        add_lines(lines, [
            line("Covers Management Fees", safe_val(row, "cmf")),
            line("Covers Admin Expenses", safe_val(row, "cae")),
            line("Covers Franchise Taxes", safe_val(row, "cft")),
            line("Management Fee Recipient Scope", safe_val(row, "mfrs")),
            line("Requires Arms Length", safe_val(row, "ral")),
            line("Requires Board Approval", safe_val(row, "rba")),
            line("Default Condition", safe_val(row, "dc")),
        ])
        all_baskets.append(lines)

    # Query 7: Equity award basket
    q_eqaward = f'''
        match
            $p isa rp_provision, has provision_id "{provision_id}";
            (provision: $p, basket: $b) isa provision_has_basket;
            $b isa equity_award_basket, has basket_id $bid;
            try {{ $b has section_reference $sec; }};
            try {{ $b has source_page $pg; }};
            try {{ $b has covers_cashless_exercise $cce; }};
            try {{ $b has covers_tax_withholding $ctw; }};
            try {{ $b has default_condition $dc; }};
        select $b, $bid, $sec, $pg, $cce, $ctw, $dc;
    '''
    for row in run_query(q_eqaward, trace=trace, trace_name="rp_basket_equity_award"):
        lines = ["### Equity Award Basket"]
        sec, pg = safe_val(row, "sec"), safe_val(row, "pg")
        if sec or pg is not None:
            lines.append(f"  {', '.join(filter(None, [f'Section: {sec}' if sec else None, f'Page: {pg}' if pg is not None else None]))}")
        add_lines(lines, [
            line("Covers Cashless Exercise", safe_val(row, "cce")),
            line("Covers Tax Withholding", safe_val(row, "ctw")),
            line("Default Condition", safe_val(row, "dc")),
        ])
        all_baskets.append(lines)

    # Query 8: Unsub distribution basket (Section 6.06(p) carve-out)
    q_unsub_dist = f'''
        match
            $p isa rp_provision, has provision_id "{provision_id}";
            (provision: $p, basket: $b) isa provision_has_basket;
            $b isa unsub_distribution_basket, has basket_id $bid;
            try {{ $b has section_reference $sec; }};
            try {{ $b has source_page $pg; }};
            try {{ $b has covers_equity_interests $cei; }};
            try {{ $b has covers_indebtedness $ci; }};
            try {{ $b has covers_assets $ca; }};
            try {{ $b has covers_proceeds $cp; }};
            try {{ $b has is_categorical $ic; }};
            try {{ $b has requires_valid_designation $rvd; }};
        select $b, $bid, $sec, $pg, $cei, $ci, $ca, $cp, $ic, $rvd;
    '''
    for row in run_query(q_unsub_dist, trace=trace, trace_name="rp_basket_unsub_dist"):
        lines = ["### Unsub Distribution Basket"]
        sec, pg = safe_val(row, "sec"), safe_val(row, "pg")
        if sec or pg is not None:
            lines.append(f"  {', '.join(filter(None, [f'Section: {sec}' if sec else None, f'Page: {pg}' if pg is not None else None]))}")
        add_lines(lines, [
            line("Covers Equity Interests", safe_val(row, "cei"),
                  entity_type="unsub_distribution_basket", attr_key="covers_equity_interests"),
            line("Covers Indebtedness", safe_val(row, "ci")),
            line("Covers Assets", safe_val(row, "ca"),
                  entity_type="unsub_distribution_basket", attr_key="covers_assets"),
            line("Covers Proceeds", safe_val(row, "cp")),
            line("Is Categorical", safe_val(row, "ic"),
                  entity_type="unsub_distribution_basket", attr_key="is_categorical"),
            line("Requires Valid Designation", safe_val(row, "rvd")),
        ])
        all_baskets.append(lines)

    if not all_baskets:
        return []

    result = ["## RP Baskets"]
    for basket_lines in all_baskets:
        result.append("")
        result.extend(basket_lines)
    return result


def fetch_builder_sources(provision_id: str, trace: TraceCollector = None) -> List[str]:
    """Fetch builder basket sources."""
    query = f'''
        match
            $p isa rp_provision, has provision_id "{provision_id}";
            (provision: $p, basket: $bb) isa provision_has_basket;
            $bb isa builder_basket;
            (basket: $bb, source: $s) isa basket_has_source;
            $s has source_id $sid;
            try {{ $s has source_name $sn; }};
            try {{ $s has section_reference $sec; }};
            try {{ $s has source_page $pg; }};
            try {{ $s has not_otherwise_applied $noa; }};
            try {{ $s has dollar_amount $da; }};
            try {{ $s has ebitda_percentage $ep; }};
            try {{ $s has uses_greater_of $ugo; }};
            try {{ $s has percentage $pct; }};
            try {{ $s has is_primary_test $ipt; }};
            try {{ $s has retained_ecf_formula $recf; }};
            try {{ $s has lookback_period $lbp; }};
            try {{ $s has lookback_quarters $lbq; }};
            try {{ $s has fc_multiplier $fcm; }};
            try {{ $s has excludes_cure_contributions $ecc; }};
            try {{ $s has excludes_disqualified_stock $eds; }};
            try {{ $s has sweep_section_reference $ssr; }};
            try {{ $s has has_ratio_disposition_basket $hrdb; }};
            try {{ $s has ratio_disposition_threshold $rdt; }};
        select $s, $sid, $sn, $sec, $pg, $noa,
               $da, $ep, $ugo, $pct, $ipt,
               $recf, $lbp, $lbq, $fcm, $ecc, $eds,
               $ssr, $hrdb, $rdt;
    '''
    rows = run_query(query, trace=trace, trace_name="builder_sources")
    if not rows:
        return []

    lines = ["## Builder Basket Sources"]
    for row in rows:
        stype = safe_type(row, "s") or "builder_basket_source"
        sname = safe_val(row, "sn") or stype.replace("_", " ").title()
        lines.append(f"\n### {sname}")

        add_lines(lines, [
            line("Dollar Amount", safe_val(row, "da"), fmt_dollar,
                  entity_type=stype, attr_key="dollar_amount"),
            line("EBITDA Percentage", safe_val(row, "ep"), fmt_pct,
                  entity_type=stype, attr_key="ebitda_percentage"),
            line("Uses Greater Of", safe_val(row, "ugo"),
                  entity_type=stype, attr_key="uses_greater_of"),
            line("Percentage", safe_val(row, "pct"), fmt_pct,
                  entity_type=stype, attr_key="percentage"),
            line("Is Primary Test", safe_val(row, "ipt")),
            line("Retained ECF Formula", safe_val(row, "recf"),
                  entity_type=stype, attr_key="retained_ecf_formula"),
            line("Lookback Period", safe_val(row, "lbp")),
            line("Lookback Quarters", safe_val(row, "lbq")),
            line("FC Multiplier", safe_val(row, "fcm"),
                  entity_type=stype, attr_key="fc_multiplier"),
            line("Excludes Cure Contributions", safe_val(row, "ecc")),
            line("Excludes Disqualified Stock", safe_val(row, "eds")),
            line("Not Otherwise Applied", safe_val(row, "noa")),
            line("Sweep Section Reference", safe_val(row, "ssr")),
            line("Has Ratio Disposition Basket", safe_val(row, "hrdb")),
            line("Ratio Disposition Threshold", safe_val(row, "rdt")),
        ])

        sec = safe_val(row, "sec")
        pg = safe_val(row, "pg")
        loc = []
        if sec:
            loc.append(f"Section: {sec}")
        if pg is not None:
            loc.append(f"Page: {pg}")
        if loc:
            lines.append(f"  {', '.join(loc)}")

    return lines


def fetch_basket_amounts(provision_id: str, trace: TraceCollector = None) -> Dict[str, tuple]:
    """Fetch basket_amount_usd and grower_pct for RP + RDP baskets.

    Returns dict of short_name -> (amount_usd, grower_pct_or_None).
    """
    result: Dict[str, tuple] = {}
    # RP baskets
    q_rp = f'''
        match
            $p isa rp_provision, has provision_id "{provision_id}";
            (provision: $p, basket: $b) isa provision_has_basket;
            $b has basket_id $bid;
            $b has basket_amount_usd $bau;
            try {{ $b has basket_grower_pct $bgp; }};
        select $bid, $bau, $bgp;
    '''
    for row in run_query(q_rp, trace=trace, trace_name="basket_amounts_rp"):
        bid = safe_val(row, "bid")
        amt = safe_val(row, "bau")
        gp = safe_val(row, "bgp")
        if bid and amt:
            short = bid.replace(f"_{provision_id}", "").replace("_", " ")
            result[short] = (amt, gp)
    # RDP baskets (for reallocation source lookup — "rdp" maps to general_rdp)
    q_rdp = f'''
        match
            $p isa rp_provision, has provision_id "{provision_id}";
            (provision: $p, rdp_basket: $rb) isa provision_has_rdp_basket;
            $rb has basket_id $bid;
            $rb has basket_amount_usd $bau;
            try {{ $rb has basket_grower_pct $bgp; }};
        select $bid, $bau, $bgp;
    '''
    for row in run_query(q_rdp, trace=trace, trace_name="basket_amounts_rdp"):
        bid = safe_val(row, "bid")
        amt = safe_val(row, "bau")
        gp = safe_val(row, "bgp")
        if bid and amt:
            short = bid.replace(f"_{provision_id}", "").replace("_", " ")
            result[short] = (amt, gp)
            if "rdp" in short and "general" in short:
                result["rdp"] = (amt, gp)
    return result


def fetch_reallocations(provision_id: str, trace: TraceCollector = None) -> List[str]:
    """Fetch basket reallocation entities."""
    query = f'''
        match
            $p isa rp_provision, has provision_id "{provision_id}";
            (provision: $p, reallocation: $r) isa provision_has_reallocation;
            $r has reallocation_id $rid;
            try {{ $r has reallocation_source $rsrc; }};
            try {{ $r has reallocation_amount_usd $ramt; }};
            try {{ $r has is_bidirectional $bidir; }};
            try {{ $r has reduces_source_basket $rsb; }};
            try {{ $r has reduction_is_dollar_for_dollar $rdfd; }};
            try {{ $r has reduction_while_outstanding_only $rwoo; }};
            try {{ $r has section_reference $sec; }};
            try {{ $r has source_page $pg; }};
        select $rid, $rsrc, $ramt, $bidir, $rsb, $rdfd, $rwoo, $sec, $pg;
    '''
    rows = run_query(query, trace=trace, trace_name="reallocations")
    if not rows:
        return []

    # Build a map of basket_id -> amount for inline display
    basket_amt_map = fetch_basket_amounts(provision_id, trace=trace)

    lines = ["## Reallocation Paths"]
    for row in rows:
        src = safe_val(row, "rsrc") or "unknown"
        bidir = safe_val(row, "bidir")
        sec = safe_val(row, "sec") or ""
        ramt = safe_val(row, "ramt")
        direction = "↔ bidirectional" if bidir else "→ one-way"
        sec_str = f" via {sec}" if sec else ""
        # Use reallocation_amount_usd for inline display, fall back to basket lookup
        src_name = src.split(" -> ")[0].strip() if " -> " in src else src
        basket_info = basket_amt_map.get(src_name)
        amt_val = ramt or (basket_info[0] if basket_info else None)
        grower_pct = basket_info[1] if basket_info else None
        if amt_val and grower_pct:
            pct_display = f"{int(grower_pct * 100)}%" if grower_pct <= 1 else f"{grower_pct}%"
            amt_str = f" ({fmt_dollar(amt_val)} / {pct_display} EBITDA)"
        elif amt_val:
            amt_str = f" ({fmt_dollar(amt_val)})"
        else:
            amt_str = ""
        lines.append(f"  {src}{amt_str}{sec_str}, {direction}")

        # Annotate with source-specific question text
        qid = _get_annotation_map().get("reallocation", {}).get(src_name)
        if qid:
            qt = _get_question_texts().get(qid)
            if qt:
                lines.append(f'    \u2192 "{qt}"')

        add_lines(lines, [
            line("    Reduces Source Basket", safe_val(row, "rsb")),
            line("    Dollar-for-Dollar Reduction", safe_val(row, "rdfd")),
            line("    While Outstanding Only", safe_val(row, "rwoo")),
        ])

    return lines


def fetch_rdp_baskets(provision_id: str, trace: TraceCollector = None) -> List[str]:
    """Fetch all RDP basket subtypes."""
    query = f'''
        match
            $p isa rp_provision, has provision_id "{provision_id}";
            (provision: $p, rdp_basket: $rb) isa provision_has_rdp_basket;
            $rb has basket_id $bid;
            try {{ $rb has section_reference $sec; }};
            try {{ $rb has source_page $pg; }};
            try {{ $rb has default_condition $dc; }};
            try {{ $rb has basket_amount_usd $bau; }};
            try {{ $rb has basket_grower_pct $bgp; }};
            try {{ $rb has ratio_threshold $rt; }};
            try {{ $rb has ratio_type $rty; }};
            try {{ $rb has is_unlimited_if_met $ium; }};
            try {{ $rb has test_date_type $tdt; }};
            try {{ $rb has pro_forma_basis $pfb; }};
            try {{ $rb has uses_closing_ratio_alternative $ucra; }};
            try {{ $rb has shares_with_rp_builder $swrp; }};
            try {{ $rb has subject_to_intercreditor $sti; }};
            try {{ $rb has requires_same_or_lower_priority $rslp; }};
            try {{ $rb has requires_same_or_later_maturity $rslm; }};
            try {{ $rb has requires_no_increase_in_principal $rnip; }};
            try {{ $rb has permits_refinancing_with_equity $prwe; }};
            try {{ $rb has requires_qualified_stock_only $rqso; }};
            try {{ $rb has requires_cash_common_equity $rcce; }};
            try {{ $rb has not_otherwise_applied $noa; }};
        select $rb, $bid, $sec, $pg, $dc,
               $bau, $bgp,
               $rt, $rty, $ium, $tdt, $pfb, $ucra,
               $swrp, $sti,
               $rslp, $rslm, $rnip, $prwe, $rqso, $rcce, $noa;
    '''
    rows = run_query(query, trace=trace, trace_name="rdp_baskets")
    if not rows:
        return []

    lines = ["## RDP Baskets"]
    for row in rows:
        rtype = safe_type(row, "rb") or "rdp_basket"
        label = rtype.replace("_", " ").title()
        lines.append(f"\n### {label}")

        sec = safe_val(row, "sec")
        pg = safe_val(row, "pg")
        loc = []
        if sec:
            loc.append(f"Section: {sec}")
        if pg is not None:
            loc.append(f"Page: {pg}")
        if loc:
            lines.append(f"  {', '.join(loc)}")

        add_lines(lines, [
            line("Basket Amount", safe_val(row, "bau"), fmt_dollar),
            line("Grower Pct", safe_val(row, "bgp"), fmt_pct),
            line("Ratio Threshold", safe_val(row, "rt")),
            line("Ratio Type", safe_val(row, "rty")),
            line("Is Unlimited If Met", safe_val(row, "ium")),
            line("Test Date Type", safe_val(row, "tdt")),
            line("Pro Forma Basis", safe_val(row, "pfb")),
            line("Uses Closing Ratio Alternative", safe_val(row, "ucra")),
            line("Shares With RP Builder", safe_val(row, "swrp")),
            line("Subject To Intercreditor", safe_val(row, "sti")),
            line("Requires Same Or Lower Priority", safe_val(row, "rslp")),
            line("Requires Same Or Later Maturity", safe_val(row, "rslm")),
            line("Requires No Increase In Principal", safe_val(row, "rnip")),
            line("Permits Refinancing With Equity", safe_val(row, "prwe")),
            line("Requires Qualified Stock Only", safe_val(row, "rqso")),
            line("Requires Cash Common Equity", safe_val(row, "rcce")),
            line("Not Otherwise Applied", safe_val(row, "noa")),
            line("Default Condition", safe_val(row, "dc")),
        ])

    return lines


def fetch_jcrew_blocker(provision_id: str, trace: TraceCollector = None) -> List[str]:
    """Fetch J.Crew blocker + exceptions."""
    blocker_query = f'''
        match
            $p isa rp_provision, has provision_id "{provision_id}";
            (provision: $p, blocker: $b) isa provision_has_blocker;
            $b has blocker_id $bid;
            try {{ $b has covers_transfer $ct; }};
            try {{ $b has covers_designation $cd; }};
            try {{ $b has covers_ip $cip; }};
            try {{ $b has covers_material_assets $cma; }};
            try {{ $b has covers_exclusive_licensing $cel; }};
            try {{ $b has covers_nonexclusive_licensing $cnl; }};
            try {{ $b has covers_pledge $cpledge; }};
            try {{ $b has covers_abandonment $cab; }};
            try {{ $b has binds_loan_parties $blp; }};
            try {{ $b has binds_restricted_subs $brs; }};
            try {{ $b has is_sacred_right $isr; }};
            try {{ $b has section_reference $sec; }};
            try {{ $b has source_page $pg; }};
        select $b, $bid, $ct, $cd, $cip, $cma, $cel, $cnl, $cpledge, $cab,
               $blp, $brs, $isr, $sec, $pg;
    '''
    rows = run_query(blocker_query, trace=trace, trace_name="jcrew_blocker")
    if not rows:
        return []

    row = rows[0]
    lines = ["## J.Crew Blocker"]

    sec = safe_val(row, "sec")
    pg = safe_val(row, "pg")
    loc = []
    if sec:
        loc.append(f"Section: {sec}")
    if pg is not None:
        loc.append(f"Page: {pg}")
    if loc:
        lines.append(f"  {', '.join(loc)}")

    add_lines(lines, [
        line("Covers Transfer", safe_val(row, "ct"),
              entity_type="jcrew_blocker", attr_key="covers_transfer"),
        line("Covers Designation", safe_val(row, "cd"),
              entity_type="jcrew_blocker", attr_key="covers_designation"),
        line("Covers IP", safe_val(row, "cip"),
              entity_type="jcrew_blocker", attr_key="covers_ip"),
        line("Covers Material Assets", safe_val(row, "cma"),
              entity_type="jcrew_blocker", attr_key="covers_material_assets"),
        line("Covers Exclusive Licensing", safe_val(row, "cel")),
        line("Covers Non-Exclusive Licensing", safe_val(row, "cnl")),
        line("Covers Pledge", safe_val(row, "cpledge")),
        line("Covers Abandonment", safe_val(row, "cab")),
        line("Binds Loan Parties", safe_val(row, "blp")),
        line("Binds Restricted Subs", safe_val(row, "brs")),
        line("Is Sacred Right", safe_val(row, "isr"),
              entity_type="jcrew_blocker", attr_key="is_sacred_right"),
    ])

    # Fetch exceptions
    bid = safe_val(row, "bid")
    if bid:
        exc_query = f'''
            match
                $b isa jcrew_blocker, has blocker_id "{bid}";
                (blocker: $b, exception: $e) isa blocker_has_exception;
                $e has exception_id $eid;
                try {{ $e has exception_name $en; }};
                try {{ $e has scope_limitation $sl; }};
                try {{ $e has section_reference $esec; }};
                try {{ $e has source_page $epg; }};
            select $e, $eid, $en, $sl, $esec, $epg;
        '''
        exc_rows = run_query(exc_query, trace=trace, trace_name="blocker_exceptions")
        if exc_rows:
            lines.append("")
            lines.append("### Exceptions")
            for erow in exc_rows:
                etype = safe_type(erow, "e") or "blocker_exception"
                ename = safe_val(erow, "en") or etype.replace("_", " ").title()
                lines.append(f"\n  Exception: {ename}")
                add_lines(lines, [
                    line("    Type", etype),
                    line("    Scope Limitation", safe_val(erow, "sl")),
                ])
                esec = safe_val(erow, "esec")
                epg = safe_val(erow, "epg")
                eloc = []
                if esec:
                    eloc.append(f"Section: {esec}")
                if epg is not None:
                    eloc.append(f"Page: {epg}")
                if eloc:
                    lines.append(f"    {', '.join(eloc)}")

    return lines


def fetch_investment_pathways(provision_id: str, trace: TraceCollector = None) -> List[str]:
    """Fetch investment pathways."""
    query = f'''
        match
            $p isa rp_provision, has provision_id "{provision_id}";
            (provision: $p, pathway: $pw) isa provision_has_pathway;
            $pw has pathway_id $pid;
            try {{ $pw has pathway_source_type $pst; }};
            try {{ $pw has pathway_target_type $ptt; }};
            try {{ $pw has cap_dollar_usd $cdu; }};
            try {{ $pw has cap_pct_total_assets $cpta; }};
            try {{ $pw has cap_uses_greater_of $cugo; }};
            try {{ $pw has is_uncapped $iu; }};
            try {{ $pw has can_stack_with_other_baskets $csob; }};
            try {{ $pw has section_reference $sec; }};
            try {{ $pw has source_page $pg; }};
        select $pid, $pst, $ptt, $cdu, $cpta, $cugo, $iu, $csob, $sec, $pg;
    '''
    rows = run_query(query, trace=trace, trace_name="investment_pathways")
    if not rows:
        return []

    lines = ["## Investment Pathways"]
    for row in rows:
        src = safe_val(row, "pst") or "?"
        tgt = safe_val(row, "ptt") or "?"
        lines.append(f"\n### {src} -> {tgt}")

        add_lines(lines, [
            line("Dollar Cap", safe_val(row, "cdu"), fmt_dollar),
            line("Pct Total Assets Cap", safe_val(row, "cpta"), fmt_pct),
            line("Cap Uses Greater Of", safe_val(row, "cugo")),
            line("Is Uncapped", safe_val(row, "iu")),
            line("Can Stack With Other Baskets", safe_val(row, "csob")),
        ])

        sec = safe_val(row, "sec")
        pg = safe_val(row, "pg")
        loc = []
        if sec:
            loc.append(f"Section: {sec}")
        if pg is not None:
            loc.append(f"Page: {pg}")
        if loc:
            lines.append(f"  {', '.join(loc)}")

    return lines


def fetch_unsub_designation(provision_id: str, trace: TraceCollector = None) -> List[str]:
    """Fetch unsub designation entity."""
    query = f'''
        match
            $p isa rp_provision, has provision_id "{provision_id}";
            (provision: $p, designation: $d) isa provision_has_unsub;
            $d has designation_id $did;
            try {{ $d has requires_no_default $rnd; }};
            try {{ $d has requires_board_approval $rba; }};
            try {{ $d has dollar_cap_usd $dcu; }};
            try {{ $d has pct_cap_assets $pca; }};
            try {{ $d has redesignation_permitted $rp; }};
            try {{ $d has section_reference $sec; }};
            try {{ $d has source_page $pg; }};
        select $did, $rnd, $rba, $dcu, $pca, $rp, $sec, $pg;
    '''
    rows = run_query(query, trace=trace, trace_name="unsub_designation")
    if not rows:
        return []

    row = rows[0]
    lines = ["## Unsub Designation"]

    sec = safe_val(row, "sec")
    pg = safe_val(row, "pg")
    loc = []
    if sec:
        loc.append(f"Section: {sec}")
    if pg is not None:
        loc.append(f"Page: {pg}")
    if loc:
        lines.append(f"  {', '.join(loc)}")

    add_lines(lines, [
        line("Requires No Default", safe_val(row, "rnd")),
        line("Requires Board Approval", safe_val(row, "rba")),
        line("Dollar Cap", safe_val(row, "dcu"), fmt_dollar,
              entity_type="unsub_designation", attr_key="dollar_cap_usd"),
        line("Pct Cap Total Assets", safe_val(row, "pca"), fmt_pct,
              entity_type="unsub_designation", attr_key="pct_cap_assets"),
        line("Redesignation Permitted", safe_val(row, "rp")),
    ])

    return lines


def fetch_sweep_tiers(provision_id: str, trace: TraceCollector = None) -> List[str]:
    """Fetch sweep tiers."""
    query = f'''
        match
            $p isa rp_provision, has provision_id "{provision_id}";
            (provision: $p, tier: $t) isa provision_has_sweep_tier;
            $t has tier_id $tid;
            try {{ $t has leverage_threshold $lt; }};
            try {{ $t has sweep_percentage $sp; }};
            try {{ $t has is_highest_tier $iht; }};
            try {{ $t has section_reference $sec; }};
            try {{ $t has source_page $pg; }};
        select $tid, $lt, $sp, $iht, $sec, $pg;
    '''
    rows = run_query(query, trace=trace, trace_name="sweep_tiers")
    if not rows:
        return []

    lines = ["## Sweep Tiers"]
    for i, row in enumerate(rows, 1):
        lines.append(f"\n### Tier {i}")
        add_lines(lines, [
            line("Leverage Threshold", safe_val(row, "lt")),
            line("Sweep Percentage", safe_val(row, "sp"), fmt_pct),
            line("Is Highest Tier", safe_val(row, "iht")),
        ])
        sec = safe_val(row, "sec")
        pg = safe_val(row, "pg")
        loc = []
        if sec:
            loc.append(f"Section: {sec}")
        if pg is not None:
            loc.append(f"Page: {pg}")
        if loc:
            lines.append(f"  {', '.join(loc)}")

    return lines


def fetch_de_minimis(provision_id: str, trace: TraceCollector = None) -> List[str]:
    """Fetch de minimis thresholds."""
    query = f'''
        match
            $p isa rp_provision, has provision_id "{provision_id}";
            (provision: $p, threshold: $t) isa provision_has_de_minimis;
            $t has threshold_id $tid;
            try {{ $t has threshold_type $ttype; }};
            try {{ $t has threshold_amount_usd $tamt; }};
            try {{ $t has section_reference $sec; }};
            try {{ $t has source_page $pg; }};
        select $tid, $ttype, $tamt, $sec, $pg;
    '''
    rows = run_query(query, trace=trace, trace_name="de_minimis_thresholds")
    if not rows:
        return []

    lines = ["## De Minimis Thresholds"]
    for row in rows:
        ttype = safe_val(row, "ttype") or "threshold"
        lines.append(f"\n### {ttype}")
        add_lines(lines, [
            line("Amount", safe_val(row, "tamt"), fmt_dollar),
        ])
        sec = safe_val(row, "sec")
        pg = safe_val(row, "pg")
        loc = []
        if sec:
            loc.append(f"Section: {sec}")
        if pg is not None:
            loc.append(f"Page: {pg}")
        if loc:
            lines.append(f"  {', '.join(loc)}")

    return lines
