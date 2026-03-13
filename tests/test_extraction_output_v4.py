"""Tests for unified extraction response schema (Phase 2d-ii)."""
import pytest
from app.schemas.extraction_response import Answer, ExtractionResponse


class TestAnswer:
    """Test Answer model."""

    def test_scalar_boolean(self):
        a = Answer(question_id="rp_a1", value=True, answer_type="boolean")
        assert a.value is True
        assert a.confidence == "high"

    def test_scalar_number(self):
        a = Answer(question_id="rp_b2", value=130000000, answer_type="number")
        assert a.value == 130000000

    def test_scalar_string(self):
        a = Answer(question_id="rp_c1", value="first_lien", answer_type="string")
        assert a.value == "first_lien"

    def test_multiselect(self):
        a = Answer(
            question_id="rp_d1",
            value=["term_loans", "revolver"],
            answer_type="multiselect",
        )
        assert isinstance(a.value, list)
        assert len(a.value) == 2

    def test_entity_list(self):
        a = Answer(
            question_id="rp_el_sweep_tiers",
            value=[
                {"leverage_threshold": 5.75, "sweep_percentage": 0.5, "is_highest_tier": True},
                {"leverage_threshold": 5.5, "sweep_percentage": 0.0},
            ],
            answer_type="entity_list",
        )
        assert isinstance(a.value, list)
        assert len(a.value) == 2
        assert a.value[0]["leverage_threshold"] == 5.75

    def test_provenance_fields(self):
        a = Answer(
            question_id="rp_a1",
            value=True,
            answer_type="boolean",
            confidence="medium",
            source_text="exact quote here",
            source_page=145,
            reasoning="inferred from Section 6.06",
        )
        assert a.source_text == "exact quote here"
        assert a.source_page == 145
        assert a.reasoning is not None


class TestExtractionResponse:
    """Test ExtractionResponse model."""

    def test_empty(self):
        r = ExtractionResponse()
        assert r.answers == []

    def test_with_mixed_answers(self):
        r = ExtractionResponse(answers=[
            Answer(question_id="rp_a1", value=True, answer_type="boolean"),
            Answer(question_id="rp_b1", value=5.75, answer_type="number"),
            Answer(
                question_id="rp_el_sweep_tiers",
                value=[{"leverage_threshold": 5.75, "sweep_percentage": 0.5}],
                answer_type="entity_list",
            ),
        ])
        assert len(r.answers) == 3
        types = {a.answer_type for a in r.answers}
        assert types == {"boolean", "number", "entity_list"}

    def test_model_validate(self):
        """Test parsing from raw dict (as from JSON)."""
        data = {
            "answers": [
                {"question_id": "rp_a1", "value": True, "answer_type": "boolean"},
                {"question_id": "rp_el_de_minimis", "value": [
                    {"threshold_type": "individual", "dollar_amount": 20000000}
                ], "answer_type": "entity_list"},
            ]
        }
        r = ExtractionResponse.model_validate(data)
        assert len(r.answers) == 2
        assert r.answers[0].value is True
        assert r.answers[1].value[0]["threshold_type"] == "individual"
