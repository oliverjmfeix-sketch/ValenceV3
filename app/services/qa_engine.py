"""
Q&A Engine — DEPRECATED.

This module is NOT used by any production code path.
The production Q&A endpoint is POST /api/deals/{deal_id}/ask in deals.py,
which uses TopicRouter (app/services/topic_router.py) for SSoT-compliant
question routing and Claude for synthesis.

This file is kept only for backward compatibility.  Do NOT add new code here.
Do NOT import this module in new code.

HISTORY OF SSoT VIOLATIONS (now removed):
- _identify_relevant_attributes(): hardcoded keyword→attribute mappings
- _get_provision_type(): hardcoded mfn_attributes set
- _parse_cross_deal_query(): hardcoded attribute→condition mappings

All of these are now replaced by TopicRouter which queries TypeDB at runtime.
"""
import logging

logger = logging.getLogger(__name__)
logger.warning(
    "qa_engine.py is DEPRECATED. Use POST /api/deals/{deal_id}/ask "
    "and app.services.topic_router.TopicRouter instead."
)
