"""
Health check endpoints - Simplified
"""
from typing import Dict, Any
from fastapi import APIRouter
from typedb.driver import TransactionType

from app.config import settings
from app.services.typedb_client import typedb_client

router = APIRouter(tags=["Health"])


@router.get("/api/admin/cost-summary")
async def cost_summary() -> Dict[str, Any]:
    """Return pricing table and expected per-doc costs for reference."""
    from app.services.cost_tracker import MODEL_PRICING
    return {
        "model_pricing_per_1k_tokens": MODEL_PRICING,
        "expected_costs": {
            "segmentation": "~$0.76 (Sonnet, ~250K input tokens)",
            "rp_extraction": "~$0.20 (Sonnet, ~40K input tokens)",
            "mfn_extraction": "~$0.15 (Sonnet, ~30K input tokens)",
            "qa_question": "~$0.01 (Sonnet, ~3K input tokens)",
            "total_per_document": "~$0.96-$1.10",
        },
        "note": "Actual per-call costs are logged as structured JSON (event=claude_api_cost). "
                "Filter Railway logs with: railway logs | grep claude_api_cost",
    }


@router.get("/api/admin/ssot-status")
async def ssot_status() -> Dict[str, Any]:
    """Live SSoT verification — returns counts of all TypeDB-sourced data."""
    from app.services.segment_introspector import get_segment_types

    segments = get_segment_types()

    counts = {
        "segment_types": len(segments),
        "extraction_metadata": 0,
        "ontology_questions": 0,
        "ontology_categories": 0,
        "legal_concepts": 0,
    }

    try:
        tx = typedb_client.driver.transaction(
            settings.typedb_database, TransactionType.READ
        )
        try:
            for key, query in [
                ("extraction_metadata", "match $em isa extraction_metadata; select $em;"),
                ("ontology_questions", "match $q isa ontology_question; select $q;"),
                ("ontology_categories", "match $c isa ontology_category; select $c;"),
                ("legal_concepts", "match $c isa concept; select $c;"),
            ]:
                r = tx.query(query).resolve()
                counts[key] = len(list(r.as_concept_rows()))
        finally:
            tx.close()
    except Exception as e:
        return {"error": str(e), **counts}

    return {
        **counts,
        "ssot_compliant": counts["segment_types"] == 21 and counts["extraction_metadata"] >= 19,
    }


def _safe_get_value(row, key: str, default=None):
    """Safely get attribute value from a TypeDB row with null check."""
    try:
        concept = row.get(key)
        if concept is None:
            return default
        return concept.as_attribute().get_value()
    except Exception:
        return default


@router.get("/health")
async def health_check() -> Dict[str, Any]:
    """Basic health check."""
    return {
        "status": "ok" if typedb_client.is_connected else "degraded",
        "version": "3.0.0",
        "typedb_connected": typedb_client.is_connected
    }


@router.get("/api/health")
async def api_health_check() -> Dict[str, Any]:
    """API health check (with /api prefix)."""
    return {
        "status": "ok" if typedb_client.is_connected else "degraded",
        "version": "3.0.0",
        "typedb_connected": typedb_client.is_connected
    }


@router.get("/api/debug/schema-check")
async def debug_schema_check() -> Dict[str, Any]:
    """Check if expanded schema types exist."""
    driver = typedb_client.driver
    db_name = settings.typedb_database
    results = {}

    if not driver:
        return {"error": "No TypeDB driver"}

    # Check if we can insert a question with extraction_prompt
    # This tests if the attribute exists in the schema
    try:
        tx = driver.transaction(db_name, TransactionType.WRITE)
        test_query = """
            insert $q isa ontology_question,
                has question_id "test_extraction_prompt_check",
                has question_text "Test",
                has answer_type "boolean",
                has covenant_type "TEST",
                has display_order 999,
                has extraction_prompt "test prompt";
        """
        tx.query(test_query).resolve()
        # If we get here, extraction_prompt exists - roll back
        tx.close()
        results["extraction_prompt_exists"] = True

        # Delete the test question
        tx = driver.transaction(db_name, TransactionType.WRITE)
        delete_query = """
            match $q isa ontology_question, has question_id "test_extraction_prompt_check";
            delete $q;
        """
        tx.query(delete_query).resolve()
        tx.commit()
    except Exception as e:
        try:
            tx.close()
        except:
            pass
        error_lower = str(e).lower()
        if "extraction_prompt" in error_lower and ("unknown" in error_lower or "does not" in error_lower or "cannot" in error_lower):
            results["extraction_prompt_exists"] = False
            results["extraction_prompt_error"] = "Attribute does not exist in schema"
        else:
            results["extraction_prompt_exists"] = "unknown"
            results["extraction_prompt_error"] = str(e)[:200]

    # Check for new concept types by trying to query instances
    for concept_type in ["reallocatable_basket", "exempt_sale_type"]:
        try:
            tx = driver.transaction(db_name, TransactionType.READ)
            query = f"""
                match $c isa {concept_type};
                select $c;
            """
            result = list(tx.query(query).resolve().as_concept_rows())
            results[f"{concept_type}_count"] = len(result)
            tx.close()
        except Exception as e:
            error_lower = str(e).lower()
            if "unknown" in error_lower or "does not exist" in error_lower:
                results[f"{concept_type}_exists"] = False
            else:
                results[f"{concept_type}_error"] = str(e)[:100]

    # Count questions
    try:
        tx = driver.transaction(db_name, TransactionType.READ)
        query = """
            match $q isa ontology_question, has covenant_type "RP";
            select $q;
        """
        result = list(tx.query(query).resolve().as_concept_rows())
        results["rp_question_count"] = len(result)
        tx.close()
    except Exception as e:
        results["rp_question_count_error"] = str(e)

    # Check for new questions
    new_question_ids = ["rp_f9", "rp_l1", "rp_m1", "rp_n1", "rp_i7"]
    for qid in new_question_ids:
        try:
            tx = driver.transaction(db_name, TransactionType.READ)
            query = f"""
                match $q isa ontology_question, has question_id "{qid}";
                select $q;
            """
            result = list(tx.query(query).resolve().as_concept_rows())
            results[f"question_{qid}_exists"] = len(result) > 0
            tx.close()
        except Exception as e:
            results[f"question_{qid}_error"] = str(e)

    # Check for new categories and their question counts
    for cat_id in ["L", "M", "N"]:
        try:
            tx = driver.transaction(db_name, TransactionType.READ)
            query = f"""
                match $cat isa ontology_category, has category_id "{cat_id}";
                select $cat;
            """
            result = list(tx.query(query).resolve().as_concept_rows())
            results[f"category_{cat_id}_exists"] = len(result) > 0
            tx.close()

            # Check category_has_question relations
            tx = driver.transaction(db_name, TransactionType.READ)
            query = f"""
                match
                    $cat isa ontology_category, has category_id "{cat_id}";
                    (category: $cat, question: $q) isa category_has_question;
                select $q;
            """
            result = list(tx.query(query).resolve().as_concept_rows())
            results[f"category_{cat_id}_question_count"] = len(result)
            tx.close()
        except Exception as e:
            results[f"category_{cat_id}_error"] = str(e)[:100]

    # Count total questions with category relations
    try:
        tx = driver.transaction(db_name, TransactionType.READ)
        query = """
            match
                $q isa ontology_question, has covenant_type "RP";
                (category: $cat, question: $q) isa category_has_question;
            select $q;
        """
        result = list(tx.query(query).resolve().as_concept_rows())
        results["questions_with_category_count"] = len(result)
        tx.close()
    except Exception as e:
        results["questions_with_category_error"] = str(e)[:100]

    return results


@router.post("/api/debug/reload-schema-expanded")
async def debug_reload_schema_expanded() -> Dict[str, Any]:
    """Manually reload the expanded schema."""
    from pathlib import Path
    from app.config import settings

    driver = typedb_client.driver
    db_name = settings.typedb_database
    results = {"steps": []}

    if not driver:
        return {"error": "No TypeDB driver"}

    # Load schema_unified.tql
    DATA_DIR = Path(__file__).parent.parent / "data"
    schema_file = DATA_DIR / "schema_unified.tql"

    if not schema_file.exists():
        return {"error": f"Schema file not found: {schema_file}"}

    content = schema_file.read_text()
    lines = [l for l in content.split('\n') if l.strip() and not l.strip().startswith('#')]
    schema_tql = '\n'.join(lines)

    results["steps"].append(f"Loaded schema file: {len(lines)} lines")

    # Parse into individual definitions
    current_def = []
    definitions = []
    in_define = False

    for line in lines:
        stripped = line.strip()
        if stripped == 'define':
            in_define = True
            continue
        if not in_define or not stripped:
            continue
        current_def.append(line)
        if stripped.endswith(';'):
            definitions.append('\n'.join(current_def))
            current_def = []

    results["steps"].append(f"Parsed {len(definitions)} definitions")
    results["definitions_preview"] = [d[:60] + "..." for d in definitions[:5]]

    # Try each definition
    added = []
    skipped = []
    failed = []

    for defn in definitions:
        if defn.strip().startswith('fun '):
            continue

        tx = driver.transaction(db_name, TransactionType.SCHEMA)
        try:
            tx.query(f"define\n{defn}").resolve()
            tx.commit()
            added.append(defn[:40])
        except Exception as e:
            tx.close()
            error_msg = str(e).lower()
            if "already" in error_msg or "exists" in error_msg or "duplicate" in error_msg:
                skipped.append(defn[:40])
            else:
                failed.append({"definition": defn[:60], "error": str(e)[:150]})

    results["added"] = added
    results["added_count"] = len(added)
    results["skipped_count"] = len(skipped)
    results["failed"] = failed
    results["failed_count"] = len(failed)

    return results


@router.post("/api/debug/reload-ontology-expanded")
async def debug_reload_ontology_expanded() -> Dict[str, Any]:
    """Manually reload the expanded ontology data (questions, concepts, relations)."""
    from pathlib import Path
    from app.config import settings

    driver = typedb_client.driver
    db_name = settings.typedb_database
    results = {"steps": []}

    if not driver:
        return {"error": "No TypeDB driver"}

    # Load ontology_expanded.tql
    DATA_DIR = Path(__file__).parent.parent / "data"
    ontology_file = DATA_DIR / "ontology_expanded.tql"

    if not ontology_file.exists():
        return {"error": f"Ontology file not found: {ontology_file}"}

    content = ontology_file.read_text()
    lines = [l for l in content.split('\n') if l.strip() and not l.strip().startswith('#')]

    # Parse line by line to handle match-insert pairs correctly
    # Format: "match ... ; ... ;" followed by "insert ... ;"
    insert_statements = []
    match_insert_statements = []
    current_match = None

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        if stripped.startswith('match '):
            # Start of a new match clause - save the whole line (which may contain multiple patterns)
            current_match = stripped
        elif stripped.startswith('insert ') and current_match:
            # This is the insert part of a match-insert
            full_stmt = current_match + '\n' + stripped
            match_insert_statements.append(full_stmt)
            current_match = None
        elif stripped.startswith('insert '):
            # Standalone insert statement
            insert_statements.append(stripped)

    results["steps"].append(f"Parsed {len(insert_statements)} inserts, {len(match_insert_statements)} match-inserts")

    # Execute insert statements
    inserts_created = 0
    inserts_skipped = 0
    insert_errors = []

    for stmt in insert_statements:
        tx = driver.transaction(db_name, TransactionType.WRITE)
        try:
            tx.query(stmt).resolve()
            tx.commit()
            inserts_created += 1
        except Exception as e:
            tx.close()
            error_msg = str(e).lower()
            if "unique" in error_msg or "already" in error_msg or "duplicate" in error_msg:
                inserts_skipped += 1
            else:
                if len(insert_errors) < 5:
                    insert_errors.append({"stmt": stmt[:80], "error": str(e)[:100]})

    results["inserts_created"] = inserts_created
    results["inserts_skipped"] = inserts_skipped
    results["insert_errors"] = insert_errors

    # Execute match-insert statements (relations)
    relations_created = 0
    relations_skipped = 0
    relation_errors = []

    for stmt in match_insert_statements:
        tx = driver.transaction(db_name, TransactionType.WRITE)
        try:
            tx.query(stmt).resolve()
            tx.commit()
            relations_created += 1
        except Exception as e:
            tx.close()
            error_msg = str(e).lower()
            if "unique" in error_msg or "already" in error_msg or "duplicate" in error_msg:
                relations_skipped += 1
            else:
                if len(relation_errors) < 5:
                    relation_errors.append({"stmt": stmt[:100], "error": str(e)[:100]})

    results["relations_created"] = relations_created
    results["relations_skipped"] = relations_skipped
    results["relation_errors"] = relation_errors

    return results


@router.post("/api/debug/fix-target-fields")
async def debug_fix_target_fields() -> Dict[str, Any]:
    """Create missing target_field entities and question_targets_field relations."""
    from app.config import settings

    driver = typedb_client.driver
    db_name = settings.typedb_database
    results = {}

    if not driver:
        return {"error": "No TypeDB driver"}

    # Define all target fields for new questions
    target_fields = {
        # F10-F17
        "rp_f10": "builder_ecf_source_exists",
        "rp_f11": "builder_ecf_formula",
        "rp_f12": "builder_ebitda_fc_exists",
        "rp_f13": "builder_fc_multiplier_pct",
        "rp_f14": "builder_uses_greatest_of",
        "rp_f15": "builder_start_date_language",
        "rp_f16": "builder_asset_proceeds_source",
        "rp_f17": "builder_investment_returns_source",
        # G5-G7
        "rp_g5": "ratio_no_worse_test_exists",
        "rp_g6": "ratio_no_worse_threshold",
        "rp_g7": "ratio_multiple_tiers_exist",
        # I3-I8 (I7, I8 are the renamed ones)
        "rp_i3": "reallocation_section_ref",
        "rp_i4": "rdp_basket_reallocation_amount_usd",
        "rp_i5": "investment_basket_reallocation_amount_usd",
        "rp_i6": "reallocation_bidirectional",
        "rp_i7": "reallocation_to_rp_permitted",
        # L1-L6, L8-L9 (L7 is multiselect)
        "rp_l1": "asset_proceeds_can_fund_dividends",
        "rp_l2": "leverage_tiered_sweep_exists",
        "rp_l3": "sweep_tier_1",
        "rp_l4": "sweep_tier_2",
        "rp_l5": "de_minimis_individual_usd",
        "rp_l6": "de_minimis_annual_usd",
        "rp_l8": "ratio_basket_avoids_sweep",
        "rp_l9": "sweep_exempt_ratio_threshold",
        # M1-M3 (M4 is multiselect)
        "rp_m1": "unsub_equity_dividend_permitted",
        "rp_m2": "unsub_asset_dividend_permitted",
        "rp_m3": "unsub_distribution_section_ref",
        # N1-N3 (N4, N5 are multiselect)
        "rp_n1": "general_rp_basket_amount_usd",
        "rp_n2": "general_rp_basket_grower_pct",
        "rp_n3": "all_baskets_summary",
    }

    # Step 1: Check existing question_targets_field relations
    try:
        tx = driver.transaction(db_name, TransactionType.READ)
        query = "match (question: $q) isa question_targets_field; select $q;"
        result = list(tx.query(query).resolve().as_concept_rows())
        results["existing_question_targets_field_count"] = len(result)
        tx.close()
    except Exception as e:
        results["initial_check_error"] = str(e)[:100]

    # Step 2: Create question_targets_field relations
    # Note: question_targets_field is a RELATION with only a 'question' role
    # The target_field_name is an ATTRIBUTE on the relation, not a separate entity
    relations_created = 0
    relations_skipped = 0
    relation_errors = []

    for question_id, field_name in target_fields.items():
        tx = driver.transaction(db_name, TransactionType.WRITE)
        try:
            query = f'''
                match $q isa ontology_question, has question_id "{question_id}";
                insert (question: $q) isa question_targets_field,
                    has target_field_name "{field_name}",
                    has target_entity_type "rp_provision";
            '''
            tx.query(query).resolve()
            tx.commit()
            relations_created += 1
        except Exception as e:
            tx.close()
            error_msg = str(e).lower()
            if "unique" in error_msg or "already" in error_msg or "duplicate" in error_msg:
                relations_skipped += 1
            else:
                if len(relation_errors) < 5:
                    relation_errors.append({"question": question_id, "error": str(e)[:80]})

    results["relations_created"] = relations_created
    results["relations_skipped"] = relations_skipped
    results["relation_errors"] = relation_errors

    # Step 3: Verify final count
    try:
        tx = driver.transaction(db_name, TransactionType.READ)
        query = """
            match
                $q isa ontology_question, has question_id $qid;
                (question: $q) isa question_targets_field, has target_field_name $fn;
            select $qid, $fn;
        """
        result = list(tx.query(query).resolve().as_concept_rows())
        results["total_question_targets_field_relations"] = len(result)

        # Count new question relations specifically
        new_questions = list(target_fields.keys())
        new_count = 0
        for row in result:
            qid = _safe_get_value(row, "qid")
            if qid in new_questions:
                new_count += 1
        results["new_question_relations_count"] = new_count
        tx.close()
    except Exception as e:
        results["verification_error"] = str(e)[:100]

    return results


@router.post("/api/debug/fix-extraction-prompts")
async def debug_fix_extraction_prompts() -> Dict[str, Any]:
    """Update extraction prompts for questions with known gaps."""
    from app.config import settings

    driver = typedb_client.driver
    db_name = settings.typedb_database
    results = {"updated": [], "failed": []}

    if not driver:
        return {"error": "No TypeDB driver"}

    # Define improved extraction prompts
    prompt_updates = {
        "rp_g5": """CRITICAL: Look for TWO separate ratio tests in the RP covenant:

1. UNLIMITED THRESHOLD: Dividends unlimited if ratio <= X (e.g., 'if First Lien Leverage Ratio is less than 3.50:1.00')

2. 'NO WORSE' TEST: Dividends permitted if ratio is NOT WORSE after giving pro forma effect, even if ABOVE the unlimited threshold.

Look for these EXACT phrases:
- 'would not be greater on a pro forma basis'
- 'is equal to or less than the ratio immediately prior'
- 'no worse after giving effect'
- 'would not increase'
- 'pro forma compliance' at a DIFFERENT (higher) ratio than unlimited

The 'no worse' test is often in a SEPARATE subsection (e.g., 6.06(o)) from the unlimited ratio basket (e.g., 6.06(n)).

Answer TRUE if EITHER test exists. This is the most borrower-friendly provision - it allows dividends at ANY leverage as long as the transaction doesn't make leverage worse.""",

        "rp_g6": """If a 'no worse' ratio test exists (rp_g5 = true), extract the specific ratio threshold.

The 'no worse' test often applies at a HIGHER leverage level than the unlimited basket.

Examples:
- Unlimited dividends at <= 3.50x
- 'No worse' dividends permitted up to 6.25x (or even unlimited)

Look for:
- A ratio threshold in the 'no worse' provision that differs from the unlimited threshold
- Sometimes there's NO cap on 'no worse' (effectively unlimited leverage if not making it worse)

If 'no worse' has NO leverage cap (applies at any level), answer: 99.0 (to indicate unlimited)
If 'no worse' has a specific cap (e.g., 6.25x), answer: 6.25
If no 'no worse' test exists, answer: 0""",

        "rp_l3": """Extract the FIRST/HIGHEST leverage tier for asset sale sweep reduction.

Look in the mandatory prepayment section (usually Section 2.10 or 2.11) for TIERED sweep percentages based on leverage.

Common patterns:
- '50% of Net Cash Proceeds if First Lien Leverage Ratio is greater than 5.25:1.00 but less than or equal to 5.75:1.00'
- 'if the Consolidated First Lien Net Leverage Ratio is less than or equal to [X]:1.00, [Y]% of such Net Cash Proceeds'

FORMAT YOUR ANSWER AS: '[ratio]x = [percentage]% sweep'
Example: '5.75x = 50% sweep'

If there's only ONE threshold (not tiered), still extract it in this format.
If NO leverage-based sweep reduction exists, answer 'N/A - 100% sweep at all leverage levels'.""",

        "rp_l4": """Extract the SECOND leverage tier for asset sale sweep reduction (if it exists).

This is typically the LOWER leverage threshold where even LESS (or 0%) is swept.

Common patterns:
- '0% of Net Cash Proceeds if First Lien Leverage Ratio is less than or equal to 5.50:1.00'
- '100% of such proceeds shall be retained by the Borrower if the ratio is at or below [X]:1.00'

FORMAT YOUR ANSWER AS: '[ratio]x = [percentage]% sweep'
Example: '5.50x = 0% sweep' (meaning 100% retained by borrower)

If only ONE tier exists (captured in rp_l3), answer 'N/A - single tier only'.
If sweep goes to 0% at certain leverage, that means borrower keeps 100% of proceeds.""",

        "rp_l5": """Extract the de minimis threshold for INDIVIDUAL asset sales below which NO mandatory prepayment is required.

Look in mandatory prepayment section for language like:
- 'Net Cash Proceeds... in excess of $[X] individually'
- 'to the extent the aggregate amount exceeds $[X] for any single transaction'
- 'excluding any Asset Sale with Net Cash Proceeds of less than $[X]'

IMPORTANT: This is usually structured as 'GREATER OF':
- '$20,000,000 and 15% of Consolidated EBITDA' (common)
- '$25,000,000 and 20% of LTM EBITDA'

EXTRACT THE DOLLAR AMOUNT ONLY (the fixed floor).
Example: If 'greater of $20,000,000 and 15% of EBITDA', answer: 20000000

If no individual de minimis exists, answer: 0""",

        "rp_l6": """Extract the de minimis threshold for ANNUAL AGGREGATE asset sales below which NO mandatory prepayment is required.

This is DIFFERENT from the individual threshold - it's the yearly total.

Look for language like:
- 'in excess of $[X] in the aggregate in any fiscal year'
- 'aggregate Net Cash Proceeds... exceeding $[X] during any fiscal year'
- 'annual threshold of $[X]'

IMPORTANT: Often structured as 'GREATER OF':
- '$40,000,000 and 30% of Consolidated EBITDA' (common - usually ~2x the individual threshold)

Also check for CARRYFORWARD provisions:
- 'unused amounts may be carried forward to subsequent fiscal years'

EXTRACT THE DOLLAR AMOUNT ONLY (the fixed floor).
Example: If 'greater of $40,000,000 and 30% of EBITDA', answer: 40000000

If no annual de minimis exists (only individual), answer: 0""",
    }

    for qid, new_prompt in prompt_updates.items():
        try:
            # Delete ALL existing extraction_prompts (may be multiple from duplicate loads)
            # Keep deleting until none remain
            deleted_count = 0
            for _ in range(5):  # Max 5 iterations to avoid infinite loop
                tx = driver.transaction(db_name, TransactionType.WRITE)
                try:
                    delete_query = f'''
                        match $q isa ontology_question, has question_id "{qid}", has extraction_prompt $ep;
                        delete $ep;
                    '''
                    tx.query(delete_query).resolve()
                    tx.commit()
                    deleted_count += 1
                except Exception as del_err:
                    tx.close()
                    # No more prompts to delete or other error
                    break

            # Now insert the new prompt
            tx = driver.transaction(db_name, TransactionType.WRITE)
            # Escape the prompt for TypeQL
            escaped_prompt = new_prompt.replace('\\', '\\\\').replace('"', '\\"')
            insert_query = f'''
                match $q isa ontology_question, has question_id "{qid}";
                insert $q has extraction_prompt "{escaped_prompt}";
            '''
            tx.query(insert_query).resolve()
            tx.commit()
            results["updated"].append({"qid": qid, "deleted_old": deleted_count})
        except Exception as e:
            results["failed"].append({"qid": qid, "error": str(e)[:150]})

    # Verify the updates
    results["verification"] = {}
    for qid in prompt_updates.keys():
        try:
            tx = driver.transaction(db_name, TransactionType.READ)
            query = f'''
                match $q isa ontology_question, has question_id "{qid}", has extraction_prompt $ep;
                select $ep;
            '''
            rows = list(tx.query(query).resolve().as_concept_rows())
            if rows:
                prompt_value = _safe_get_value(rows[0], "ep", "")
                results["verification"][qid] = f"OK ({len(prompt_value)} chars)"
            else:
                results["verification"][qid] = "NOT FOUND"
            tx.close()
        except Exception as e:
            results["verification"][qid] = f"Error: {str(e)[:50]}"

    return results
