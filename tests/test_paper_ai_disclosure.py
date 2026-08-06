from research_forge.paper_ai_disclosure import AIUseEvent, build_ai_use_disclosure
from research_forge.paper_venue_policy import GENERIC_SHORT_REPORT_POLICY


def test_ai_disclosure_is_reader_facing_and_capitalizes_accountability() -> None:
    disclosure = build_ai_use_disclosure(
        study_id="study-1",
        venue_policy=GENERIC_SHORT_REPORT_POLICY,
        events=[
            AIUseEvent(
                event_id="event-1",
                model_id="model-x",
                provider="provider-x",
                step_type="draft_generation",
                purpose="draft_generation",
                generated_scientific_content=True,
                human_reviewed=True,
            )
        ],
        responsible_author="the responsible author",
    )

    assert disclosure.disclosure_text.startswith("AI-assisted tools supported")
    assert "used for model-x" not in disclosure.disclosure_text
    assert disclosure.disclosure_text.count("model-x") == 1
    assert disclosure.human_accountability_statement.startswith(
        "The responsible author reviewed"
    )
