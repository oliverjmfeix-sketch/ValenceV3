"""
Graph Reader — reads Channel 3 typed entities from TypeDB for a provision.

Returns formatted text suitable for Claude synthesis prompts.
"""
import logging
from typing import Dict, List, Any, Optional

from typedb.driver import TransactionType

from app.config import settings
from app.services.typedb_client import typedb_client

logger = logging.getLogger(__name__)


def _fmt_dollar(val) -> str:
    """Format a numeric value as $X,XXX,XXX."""
    if val is None:
        return ""
    try:
        return f"${val:,.0f}"
    except (TypeError, ValueError):
        return str(val)


def _fmt_pct(val) -> str:
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


def _safe_val(row, key: str):
    """Extract attribute value from a TypeDB row, returning None on failure."""
    try:
        concept = row.get(key)
        if concept is None:
            return None
        return concept.as_attribute().get_value()
    except Exception:
        return None


def _safe_type(row, key: str) -> Optional[str]:
    """Get the type label of an entity variable."""
    try:
        concept = row.get(key)
        if concept is None:
            return None
        return concept.as_entity().get_type().get_label()
    except Exception:
        return None


def _line(label: str, value, formatter=None) -> Optional[str]:
    """Return a formatted line if value is not None, else None."""
    if value is None:
        return None
    if formatter:
        formatted = formatter(value)
        return f"  {label}: {formatted}"
    if isinstance(value, bool):
        return f"  {label}: {'true' if value else 'false'}"
    return f"  {label}: {value}"


def _add_lines(lines: list, items: list):
    """Append non-None items to lines."""
    for item in items:
        if item is not None:
            lines.append(item)


def _run_query(query: str) -> list:
    """Execute a read query and return rows."""
    tx = typedb_client.driver.transaction(settings.typedb_database, TransactionType.READ)
    try:
        result = list(tx.query(query).resolve().as_concept_rows())
        return result
    except Exception as e:
        logger.debug(f"Query failed: {e}")
        return []
    finally:
        tx.close()


def get_rp_entities(deal_id: str) -> str:
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
    sections = []

    # ── RP Baskets ────────────────────────────────────────────────────
    basket_lines = _fetch_rp_baskets(provision_id)
    if basket_lines:
        sections.append("\n".join(basket_lines))

    # ── Builder Sources ───────────────────────────────────────────────
    source_lines = _fetch_builder_sources(provision_id)
    if source_lines:
        sections.append("\n".join(source_lines))

    # ── Reallocations ─────────────────────────────────────────────────
    realloc_lines = _fetch_reallocations(provision_id)
    if realloc_lines:
        sections.append("\n".join(realloc_lines))

    # ── RDP Baskets ───────────────────────────────────────────────────
    rdp_lines = _fetch_rdp_baskets(provision_id)
    if rdp_lines:
        sections.append("\n".join(rdp_lines))

    # ── J.Crew Blocker ────────────────────────────────────────────────
    blocker_lines = _fetch_jcrew_blocker(provision_id)
    if blocker_lines:
        sections.append("\n".join(blocker_lines))

    # ── Investment Pathways ───────────────────────────────────────────
    pathway_lines = _fetch_investment_pathways(provision_id)
    if pathway_lines:
        sections.append("\n".join(pathway_lines))

    # ── Unsub Designation ─────────────────────────────────────────────
    unsub_lines = _fetch_unsub_designation(provision_id)
    if unsub_lines:
        sections.append("\n".join(unsub_lines))

    # ── Sweep Tiers ───────────────────────────────────────────────────
    sweep_lines = _fetch_sweep_tiers(provision_id)
    if sweep_lines:
        sections.append("\n".join(sweep_lines))

    # ── De Minimis Thresholds ─────────────────────────────────────────
    dm_lines = _fetch_de_minimis(provision_id)
    if dm_lines:
        sections.append("\n".join(dm_lines))

    if not sections:
        return "(No Channel 3 entities found for this provision)"

    return "\n\n".join(sections)


# ═════════════════════════════════════════════════════════════════════════════
# INDIVIDUAL ENTITY FETCHERS
# ═════════════════════════════════════════════════════════════════════════════

def _fetch_rp_baskets(provision_id: str) -> List[str]:
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
    for row in _run_query(q_builder):
        lines = ["### Builder Basket"]
        sec, pg = _safe_val(row, "sec"), _safe_val(row, "pg")
        if sec or pg is not None:
            lines.append(f"  {', '.join(filter(None, [f'Section: {sec}' if sec else None, f'Page: {pg}' if pg is not None else None]))}")
        _add_lines(lines, [
            _line("Uses Greatest Of Tests", _safe_val(row, "ugot")),
            _line("Start Date Language", _safe_val(row, "sdl")),
            _line("Default Condition", _safe_val(row, "dc")),
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
            try {{ $b has test_date_type $tdt; }};
            try {{ $b has lct_treatment_available $lct; }};
            try {{ $b has pro_forma_basis $pfb; }};
            try {{ $b has default_condition $dc; }};
        select $b, $bid, $sec, $pg, $rt, $rty, $ium, $nwt, $nwthr, $tdt, $lct, $pfb, $dc;
    '''
    for row in _run_query(q_ratio):
        lines = ["### Ratio Basket"]
        sec, pg = _safe_val(row, "sec"), _safe_val(row, "pg")
        if sec or pg is not None:
            lines.append(f"  {', '.join(filter(None, [f'Section: {sec}' if sec else None, f'Page: {pg}' if pg is not None else None]))}")
        _add_lines(lines, [
            _line("Ratio Threshold", _safe_val(row, "rt")),
            _line("Ratio Type", _safe_val(row, "rty")),
            _line("Is Unlimited If Met", _safe_val(row, "ium")),
            _line("Has No Worse Test", _safe_val(row, "nwt")),
            _line("No Worse Threshold", _safe_val(row, "nwthr")),
            _line("Test Date Type", _safe_val(row, "tdt")),
            _line("LCT Treatment Available", _safe_val(row, "lct")),
            _line("Pro Forma Basis", _safe_val(row, "pfb")),
            _line("Default Condition", _safe_val(row, "dc")),
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
    for row in _run_query(q_general):
        lines = ["### General Rp Basket"]
        sec, pg = _safe_val(row, "sec"), _safe_val(row, "pg")
        if sec or pg is not None:
            lines.append(f"  {', '.join(filter(None, [f'Section: {sec}' if sec else None, f'Page: {pg}' if pg is not None else None]))}")
        _add_lines(lines, [
            _line("Basket Amount", _safe_val(row, "bau"), _fmt_dollar),
            _line("Grower Pct", _safe_val(row, "bgp"), _fmt_pct),
            _line("Is Per Annum", _safe_val(row, "ipa")),
            _line("Default Condition", _safe_val(row, "dc")),
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
    for row in _run_query(q_mgmt):
        lines = ["### Management Equity Basket"]
        sec, pg = _safe_val(row, "sec"), _safe_val(row, "pg")
        if sec or pg is not None:
            lines.append(f"  {', '.join(filter(None, [f'Section: {sec}' if sec else None, f'Page: {pg}' if pg is not None else None]))}")
        _add_lines(lines, [
            _line("Annual Cap", _safe_val(row, "acu"), _fmt_dollar),
            _line("Annual Cap Pct EBITDA", _safe_val(row, "acpe"), _fmt_pct),
            _line("Cap Uses Greater Of", _safe_val(row, "cugo")),
            _line("Carryforward Permitted", _safe_val(row, "cfp")),
            _line("Carryforward Max Years", _safe_val(row, "cfmy")),
            _line("Eligible Person Scope", _safe_val(row, "eps")),
            _line("Default Condition", _safe_val(row, "dc")),
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
    for row in _run_query(q_tax):
        lines = ["### Tax Distribution Basket"]
        sec, pg = _safe_val(row, "sec"), _safe_val(row, "pg")
        if sec or pg is not None:
            lines.append(f"  {', '.join(filter(None, [f'Section: {sec}' if sec else None, f'Page: {pg}' if pg is not None else None]))}")
        _add_lines(lines, [
            _line("Standalone Taxpayer Limit", _safe_val(row, "stl")),
            _line("Hypothetical Tax Rate", _safe_val(row, "htr")),
            _line("Tax Sharing Permitted", _safe_val(row, "tsp")),
            _line("Estimated Taxes Permitted", _safe_val(row, "etp")),
            _line("Default Condition", _safe_val(row, "dc")),
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
    for row in _run_query(q_holdco):
        lines = ["### Holdco Overhead Basket"]
        sec, pg = _safe_val(row, "sec"), _safe_val(row, "pg")
        if sec or pg is not None:
            lines.append(f"  {', '.join(filter(None, [f'Section: {sec}' if sec else None, f'Page: {pg}' if pg is not None else None]))}")
        _add_lines(lines, [
            _line("Covers Management Fees", _safe_val(row, "cmf")),
            _line("Covers Admin Expenses", _safe_val(row, "cae")),
            _line("Covers Franchise Taxes", _safe_val(row, "cft")),
            _line("Management Fee Recipient Scope", _safe_val(row, "mfrs")),
            _line("Requires Arms Length", _safe_val(row, "ral")),
            _line("Requires Board Approval", _safe_val(row, "rba")),
            _line("Default Condition", _safe_val(row, "dc")),
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
    for row in _run_query(q_eqaward):
        lines = ["### Equity Award Basket"]
        sec, pg = _safe_val(row, "sec"), _safe_val(row, "pg")
        if sec or pg is not None:
            lines.append(f"  {', '.join(filter(None, [f'Section: {sec}' if sec else None, f'Page: {pg}' if pg is not None else None]))}")
        _add_lines(lines, [
            _line("Covers Cashless Exercise", _safe_val(row, "cce")),
            _line("Covers Tax Withholding", _safe_val(row, "ctw")),
            _line("Default Condition", _safe_val(row, "dc")),
        ])
        all_baskets.append(lines)

    if not all_baskets:
        return []

    result = ["## RP Baskets"]
    for basket_lines in all_baskets:
        result.append("")
        result.extend(basket_lines)
    return result


def _fetch_builder_sources(provision_id: str) -> List[str]:
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
        select $s, $sid, $sn, $sec, $pg, $noa,
               $da, $ep, $ugo, $pct, $ipt,
               $recf, $lbp, $lbq, $fcm, $ecc, $eds;
    '''
    rows = _run_query(query)
    if not rows:
        return []

    lines = ["## Builder Basket Sources"]
    for row in rows:
        stype = _safe_type(row, "s") or "builder_basket_source"
        sname = _safe_val(row, "sn") or stype.replace("_", " ").title()
        lines.append(f"\n### {sname}")

        _add_lines(lines, [
            _line("Dollar Amount", _safe_val(row, "da"), _fmt_dollar),
            _line("EBITDA Percentage", _safe_val(row, "ep"), _fmt_pct),
            _line("Uses Greater Of", _safe_val(row, "ugo")),
            _line("Percentage", _safe_val(row, "pct"), _fmt_pct),
            _line("Is Primary Test", _safe_val(row, "ipt")),
            _line("Retained ECF Formula", _safe_val(row, "recf")),
            _line("Lookback Period", _safe_val(row, "lbp")),
            _line("Lookback Quarters", _safe_val(row, "lbq")),
            _line("FC Multiplier", _safe_val(row, "fcm")),
            _line("Excludes Cure Contributions", _safe_val(row, "ecc")),
            _line("Excludes Disqualified Stock", _safe_val(row, "eds")),
            _line("Not Otherwise Applied", _safe_val(row, "noa")),
        ])

        sec = _safe_val(row, "sec")
        pg = _safe_val(row, "pg")
        loc = []
        if sec:
            loc.append(f"Section: {sec}")
        if pg is not None:
            loc.append(f"Page: {pg}")
        if loc:
            lines.append(f"  {', '.join(loc)}")

    return lines


def _fetch_basket_amounts(provision_id: str) -> Dict[str, float]:
    """Fetch basket_amount_usd for all RP baskets, keyed by basket type short name."""
    query = f'''
        match
            $p isa rp_provision, has provision_id "{provision_id}";
            (provision: $p, basket: $b) isa provision_has_basket;
            $b has basket_id $bid;
            $b has basket_amount_usd $bau;
        select $bid, $bau;
    '''
    rows = _run_query(query)
    result = {}
    for row in rows:
        bid = _safe_val(row, "bid")
        amt = _safe_val(row, "bau")
        if bid and amt:
            # basket_id is like "investment_87852625_rp" — extract type prefix
            short = bid.replace(f"_{provision_id}", "").replace("_", " ")
            result[short] = amt
    return result


def _fetch_reallocations(provision_id: str) -> List[str]:
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
    rows = _run_query(query)
    if not rows:
        return []

    # Build a map of basket_id -> amount for inline display
    basket_amt_map = _fetch_basket_amounts(provision_id)

    lines = ["## Reallocation Paths"]
    for row in rows:
        src = _safe_val(row, "rsrc") or "unknown"
        bidir = _safe_val(row, "bidir")
        sec = _safe_val(row, "sec") or ""
        direction = "↔ bidirectional" if bidir else "→ one-way"
        sec_str = f" via {sec}" if sec else ""
        # Try to find source basket amount from the reallocation_source string
        # Format is "investment -> RP" so extract first part
        src_name = src.split(" -> ")[0].strip() if " -> " in src else src
        src_amt_val = basket_amt_map.get(src_name)
        src_amt = f" ({_fmt_dollar(src_amt_val)})" if src_amt_val else ""
        lines.append(f"  {src}{src_amt}{sec_str}, {direction}")

        _add_lines(lines, [
            _line("    Amount", _safe_val(row, "ramt"), _fmt_dollar),
            _line("    Reduces Source Basket", _safe_val(row, "rsb")),
            _line("    Dollar-for-Dollar Reduction", _safe_val(row, "rdfd")),
            _line("    While Outstanding Only", _safe_val(row, "rwoo")),
        ])

    return lines


def _fetch_rdp_baskets(provision_id: str) -> List[str]:
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
    rows = _run_query(query)
    if not rows:
        return []

    lines = ["## RDP Baskets"]
    for row in rows:
        rtype = _safe_type(row, "rb") or "rdp_basket"
        label = rtype.replace("_", " ").title()
        lines.append(f"\n### {label}")

        sec = _safe_val(row, "sec")
        pg = _safe_val(row, "pg")
        loc = []
        if sec:
            loc.append(f"Section: {sec}")
        if pg is not None:
            loc.append(f"Page: {pg}")
        if loc:
            lines.append(f"  {', '.join(loc)}")

        _add_lines(lines, [
            _line("Basket Amount", _safe_val(row, "bau"), _fmt_dollar),
            _line("Grower Pct", _safe_val(row, "bgp"), _fmt_pct),
            _line("Ratio Threshold", _safe_val(row, "rt")),
            _line("Ratio Type", _safe_val(row, "rty")),
            _line("Is Unlimited If Met", _safe_val(row, "ium")),
            _line("Test Date Type", _safe_val(row, "tdt")),
            _line("Pro Forma Basis", _safe_val(row, "pfb")),
            _line("Uses Closing Ratio Alternative", _safe_val(row, "ucra")),
            _line("Shares With RP Builder", _safe_val(row, "swrp")),
            _line("Subject To Intercreditor", _safe_val(row, "sti")),
            _line("Requires Same Or Lower Priority", _safe_val(row, "rslp")),
            _line("Requires Same Or Later Maturity", _safe_val(row, "rslm")),
            _line("Requires No Increase In Principal", _safe_val(row, "rnip")),
            _line("Permits Refinancing With Equity", _safe_val(row, "prwe")),
            _line("Requires Qualified Stock Only", _safe_val(row, "rqso")),
            _line("Requires Cash Common Equity", _safe_val(row, "rcce")),
            _line("Not Otherwise Applied", _safe_val(row, "noa")),
            _line("Default Condition", _safe_val(row, "dc")),
        ])

    return lines


def _fetch_jcrew_blocker(provision_id: str) -> List[str]:
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
    rows = _run_query(blocker_query)
    if not rows:
        return []

    row = rows[0]
    lines = ["## J.Crew Blocker"]

    sec = _safe_val(row, "sec")
    pg = _safe_val(row, "pg")
    loc = []
    if sec:
        loc.append(f"Section: {sec}")
    if pg is not None:
        loc.append(f"Page: {pg}")
    if loc:
        lines.append(f"  {', '.join(loc)}")

    _add_lines(lines, [
        _line("Covers Transfer", _safe_val(row, "ct")),
        _line("Covers Designation", _safe_val(row, "cd")),
        _line("Covers IP", _safe_val(row, "cip")),
        _line("Covers Material Assets", _safe_val(row, "cma")),
        _line("Covers Exclusive Licensing", _safe_val(row, "cel")),
        _line("Covers Non-Exclusive Licensing", _safe_val(row, "cnl")),
        _line("Covers Pledge", _safe_val(row, "cpledge")),
        _line("Covers Abandonment", _safe_val(row, "cab")),
        _line("Binds Loan Parties", _safe_val(row, "blp")),
        _line("Binds Restricted Subs", _safe_val(row, "brs")),
        _line("Is Sacred Right", _safe_val(row, "isr")),
    ])

    # Fetch exceptions
    bid = _safe_val(row, "bid")
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
        exc_rows = _run_query(exc_query)
        if exc_rows:
            lines.append("")
            lines.append("### Exceptions")
            for erow in exc_rows:
                etype = _safe_type(erow, "e") or "blocker_exception"
                ename = _safe_val(erow, "en") or etype.replace("_", " ").title()
                lines.append(f"\n  Exception: {ename}")
                _add_lines(lines, [
                    _line("    Type", etype),
                    _line("    Scope Limitation", _safe_val(erow, "sl")),
                ])
                esec = _safe_val(erow, "esec")
                epg = _safe_val(erow, "epg")
                eloc = []
                if esec:
                    eloc.append(f"Section: {esec}")
                if epg is not None:
                    eloc.append(f"Page: {epg}")
                if eloc:
                    lines.append(f"    {', '.join(eloc)}")

    return lines


def _fetch_investment_pathways(provision_id: str) -> List[str]:
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
    rows = _run_query(query)
    if not rows:
        return []

    lines = ["## Investment Pathways"]
    for row in rows:
        src = _safe_val(row, "pst") or "?"
        tgt = _safe_val(row, "ptt") or "?"
        lines.append(f"\n### {src} -> {tgt}")

        _add_lines(lines, [
            _line("Dollar Cap", _safe_val(row, "cdu"), _fmt_dollar),
            _line("Pct Total Assets Cap", _safe_val(row, "cpta"), _fmt_pct),
            _line("Cap Uses Greater Of", _safe_val(row, "cugo")),
            _line("Is Uncapped", _safe_val(row, "iu")),
            _line("Can Stack With Other Baskets", _safe_val(row, "csob")),
        ])

        sec = _safe_val(row, "sec")
        pg = _safe_val(row, "pg")
        loc = []
        if sec:
            loc.append(f"Section: {sec}")
        if pg is not None:
            loc.append(f"Page: {pg}")
        if loc:
            lines.append(f"  {', '.join(loc)}")

    return lines


def _fetch_unsub_designation(provision_id: str) -> List[str]:
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
    rows = _run_query(query)
    if not rows:
        return []

    row = rows[0]
    lines = ["## Unsub Designation"]

    sec = _safe_val(row, "sec")
    pg = _safe_val(row, "pg")
    loc = []
    if sec:
        loc.append(f"Section: {sec}")
    if pg is not None:
        loc.append(f"Page: {pg}")
    if loc:
        lines.append(f"  {', '.join(loc)}")

    _add_lines(lines, [
        _line("Requires No Default", _safe_val(row, "rnd")),
        _line("Requires Board Approval", _safe_val(row, "rba")),
        _line("Dollar Cap", _safe_val(row, "dcu"), _fmt_dollar),
        _line("Pct Cap Total Assets", _safe_val(row, "pca"), _fmt_pct),
        _line("Redesignation Permitted", _safe_val(row, "rp")),
    ])

    return lines


def _fetch_sweep_tiers(provision_id: str) -> List[str]:
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
    rows = _run_query(query)
    if not rows:
        return []

    lines = ["## Sweep Tiers"]
    for i, row in enumerate(rows, 1):
        lines.append(f"\n### Tier {i}")
        _add_lines(lines, [
            _line("Leverage Threshold", _safe_val(row, "lt")),
            _line("Sweep Percentage", _safe_val(row, "sp"), _fmt_pct),
            _line("Is Highest Tier", _safe_val(row, "iht")),
        ])
        sec = _safe_val(row, "sec")
        pg = _safe_val(row, "pg")
        loc = []
        if sec:
            loc.append(f"Section: {sec}")
        if pg is not None:
            loc.append(f"Page: {pg}")
        if loc:
            lines.append(f"  {', '.join(loc)}")

    return lines


def _fetch_de_minimis(provision_id: str) -> List[str]:
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
    rows = _run_query(query)
    if not rows:
        return []

    lines = ["## De Minimis Thresholds"]
    for row in rows:
        ttype = _safe_val(row, "ttype") or "threshold"
        lines.append(f"\n### {ttype}")
        _add_lines(lines, [
            _line("Amount", _safe_val(row, "tamt"), _fmt_dollar),
        ])
        sec = _safe_val(row, "sec")
        pg = _safe_val(row, "pg")
        loc = []
        if sec:
            loc.append(f"Section: {sec}")
        if pg is not None:
            loc.append(f"Page: {pg}")
        if loc:
            lines.append(f"  {', '.join(loc)}")

    return lines
