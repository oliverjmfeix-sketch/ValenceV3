"""
Cost tracking for Claude API calls.

NOTE: Model pricing is maintained here (not TypeDB) because it's
external Anthropic pricing that changes independently of our domain model.
Update rates when Anthropic changes pricing.
"""
import json
import logging
import time
from dataclasses import dataclass, field, asdict
from typing import Optional, List

logger = logging.getLogger(__name__)

# Anthropic pricing per 1K tokens (as of Feb 2025)
# Not in TypeDB because this is external vendor pricing, not domain data
MODEL_PRICING = {
    "claude-sonnet-4-20250514": {"input": 0.003, "output": 0.015},
    "claude-sonnet-4-5-20250929": {"input": 0.003, "output": 0.015},
    "claude-opus-4-20250514": {"input": 0.015, "output": 0.075},
    "claude-opus-4-5-20251101": {"input": 0.015, "output": 0.075},
    "claude-haiku-4-5-20251001": {"input": 0.0008, "output": 0.004},
}


@dataclass
class ClaudeUsage:
    """Token usage from a single Claude API call."""
    input_tokens: int
    output_tokens: int
    model: str
    step: str  # "segmentation", "rp_extraction", "mfn_extraction", "qa", "eval"
    deal_id: Optional[str] = None
    duration_seconds: Optional[float] = None

    @property
    def cost_usd(self) -> float:
        rates = MODEL_PRICING.get(self.model)
        if rates is None:
            logger.warning(
                f"COST_TRACKING_DEGRADED: Unknown model '{self.model}' — "
                f"not in MODEL_PRICING. Cost reported as $0. "
                f"Add this model to MODEL_PRICING in cost_tracker.py."
            )
            return 0.0
        return (
            (self.input_tokens / 1000) * rates["input"]
            + (self.output_tokens / 1000) * rates["output"]
        )

    def log(self):
        """Emit structured JSON log for Railway filtering."""
        logger.info(json.dumps({
            "event": "claude_api_cost",
            "deal_id": self.deal_id,
            "step": self.step,
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": round(self.cost_usd, 4),
            "duration_seconds": round(self.duration_seconds, 1) if self.duration_seconds else None,
        }))


@dataclass
class ExtractionCostSummary:
    """Aggregated cost for a full document extraction."""
    deal_id: str
    steps: List[ClaudeUsage] = field(default_factory=list)

    def add(self, usage: ClaudeUsage):
        self.steps.append(usage)

    @property
    def total_cost_usd(self) -> float:
        return sum(s.cost_usd for s in self.steps)

    @property
    def total_input_tokens(self) -> int:
        return sum(s.input_tokens for s in self.steps)

    @property
    def total_output_tokens(self) -> int:
        return sum(s.output_tokens for s in self.steps)

    def log_summary(self):
        """Emit summary log line at end of extraction."""
        logger.info(json.dumps({
            "event": "extraction_cost_summary",
            "deal_id": self.deal_id,
            "total_cost_usd": round(self.total_cost_usd, 4),
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "num_api_calls": len(self.steps),
            "steps": [
                {
                    "step": s.step,
                    "model": s.model,
                    "cost_usd": round(s.cost_usd, 4),
                    "input_tokens": s.input_tokens,
                    "output_tokens": s.output_tokens,
                }
                for s in self.steps
            ],
        }))


def extract_usage(response, model: str, step: str, deal_id: str = None,
                  duration: float = None) -> ClaudeUsage:
    """
    Extract usage from an Anthropic API response object.

    Works with both sync responses and streaming final_message.
    """
    usage = ClaudeUsage(
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
        model=model,
        step=step,
        deal_id=deal_id,
        duration_seconds=duration,
    )
    usage.log()
    return usage


# TODO: Persist extraction cost summaries to TypeDB or local storage
# so they survive log rotation. Current approach is log-only.
