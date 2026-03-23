"""
Collects trace data as the graph pipeline executes.
Lightweight — just appends to lists. No performance impact when not used.
"""
import time
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional


@dataclass
class QueryTrace:
    """Record of a single TypeDB query execution."""
    name: str              # human-readable name, e.g. "dividend_capacity_components"
    query: str             # the TQL that was executed
    row_count: int         # number of rows returned
    duration_ms: float     # wall clock time
    sample_rows: List[Dict[str, Any]] = field(default_factory=list)  # first 5 rows, serialized


@dataclass
class TraceCollector:
    """Accumulates trace data across the graph pipeline."""

    # Step 0: Question receipt
    question: str = ""
    deal_id: str = ""

    # Step 1: Covenant type routing
    covenant_type: str = ""              # "rp", "mfn", or "both"
    matched_categories: List[Dict[str, str]] = field(default_factory=list)
    routing_duration_ms: float = 0.0
    routing_fallback: str = ""           # e.g. "mfn_graph_not_available" if fell back to RP

    # Step 2: Provision lookup
    provision_id: str = ""
    provision_lookup_ms: float = 0.0

    # Step 3: TypeDB queries (entity loading)
    queries: List[QueryTrace] = field(default_factory=list)

    # Dividend capacity function (subset of Step 3)
    capacity_components: List[Dict[str, Any]] = field(default_factory=list)
    capacity_total: float = 0.0

    # Step 4: Entity context assembly
    entity_context: str = ""
    entity_context_chars: int = 0
    entity_count: int = 0

    # Step 4b: Entity filter (two-stage synthesis)
    filter_model: str = ""
    filter_input_tokens: int = 0
    filter_output_tokens: int = 0
    filter_cost_usd: float = 0.0
    filter_duration_ms: float = 0.0
    filter_entity_types: List[str] = field(default_factory=list)
    filter_total_entities: int = 0
    filter_filtered_entities: int = 0

    # Step 5+6: Claude synthesis
    claude_system_prompt: str = ""
    claude_user_prompt: str = ""
    claude_model: str = ""
    claude_input_tokens: int = 0
    claude_output_tokens: int = 0
    claude_cost_usd: float = 0.0
    claude_duration_ms: float = 0.0

    # Step 7: Answers
    claude_answer: str = ""              # verbatim Claude output
    scalar_answer: str = ""              # scalar pipeline for comparison
    scalar_context_type: str = ""

    def add_query(self, name: str, query: str, row_count: int, duration_ms: float,
                  sample_rows: List[Dict[str, Any]] = None):
        self.queries.append(QueryTrace(
            name=name,
            query=query.strip(),
            row_count=row_count,
            duration_ms=round(duration_ms, 1),
            sample_rows=sample_rows or [],
        ))

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for JSON response."""
        return {
            "step_0_question_receipt": {
                "question": self.question,
                "deal_id": self.deal_id,
            },
            "step_1_covenant_routing": {
                "covenant_type": self.covenant_type,
                "matched_categories": self.matched_categories,
                "duration_ms": round(self.routing_duration_ms, 1),
                "fallback": self.routing_fallback or None,
            },
            "step_2_provision_lookup": {
                "provision_id": self.provision_id,
                "duration_ms": round(self.provision_lookup_ms, 1),
            },
            "step_3_entity_loading": {
                "queries": [
                    {
                        "name": q.name,
                        "query": q.query,
                        "row_count": q.row_count,
                        "duration_ms": q.duration_ms,
                        "sample_rows": q.sample_rows,
                    }
                    for q in self.queries
                ],
                "total_queries": len(self.queries),
                "total_query_ms": round(sum(q.duration_ms for q in self.queries), 1),
            },
            "step_3a_dividend_capacity": {
                "total": self.capacity_total,
                "components": self.capacity_components,
            },
            "step_4_entity_context": {
                "text": self.entity_context,
                "chars": self.entity_context_chars,
            },
            "step_4b_entity_filter": {
                "model": self.filter_model,
                "input_tokens": self.filter_input_tokens,
                "output_tokens": self.filter_output_tokens,
                "cost_usd": round(self.filter_cost_usd, 4),
                "duration_ms": round(self.filter_duration_ms, 1),
                "total_entities": self.filter_total_entities,
                "filtered_entities": self.filter_filtered_entities,
                "entity_types": self.filter_entity_types,
            } if self.filter_model else None,
            "step_5_6_claude_synthesis": {
                "system_prompt": self.claude_system_prompt,
                "user_prompt": self.claude_user_prompt,
                "model": self.claude_model,
                "input_tokens": self.claude_input_tokens,
                "output_tokens": self.claude_output_tokens,
                "cost_usd": round(self.claude_cost_usd, 4),
                "duration_ms": round(self.claude_duration_ms, 1),
            } if self.claude_model else None,
            "step_7_answer": {
                "graph_answer": self.claude_answer,
                "scalar_answer": self.scalar_answer if self.scalar_answer else None,
            },
        }
