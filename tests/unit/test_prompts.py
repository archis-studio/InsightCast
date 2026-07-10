import json

from insightcast.domain.models import TranscriptSegment
from insightcast.prompts import (
    curator,
    metadata,
    selection_review,
    topic_discovery,
    translation,
)
from insightcast.prompts.serialization import (
    compact_json,
    serialize_transcript_segments_for_prompt,
)


def test_prompt_modules_have_versions_contracts_and_data_only_builders() -> None:
    assert curator.PROMPT_VERSION
    assert selection_review.PROMPT_VERSION
    assert translation.PROMPT_VERSION
    assert metadata.PROMPT_VERSION
    assert "continuous" in curator.SYSTEM_PROMPT.lower()
    assert "traditional chinese" in translation.SYSTEM_PROMPT.lower()
    assert "clickbait" in metadata.SYSTEM_PROMPT.lower()

    curator_user = curator.build_user_prompt(
        transcript=[{"start_seconds": 0, "end_seconds": 3, "text": "Hello"}],
        topics=[
            {
                "topic_id": "T1",
                "label": "Topic T1",
                "summary": "Summary",
                "central_claim": "Claim",
                "importance_reason": "Important",
                "start_seconds": 0,
                "end_seconds": 600,
                "importance_score": 0.9,
            }
        ],
        candidate_count=2,
        target_min_duration_seconds=480,
        target_max_duration_seconds=720,
        accepted_min_duration_seconds=420,
        accepted_max_duration_seconds=780,
        final_min_duration_seconds=390,
        final_max_duration_seconds=810,
        validation_feedback=None,
    )
    translation_user = translation.build_user_prompt(
        items=[{"segment_id": "s1", "text": "Hello"}]
    )
    metadata_user = metadata.build_user_prompt(
        source_title="Source",
        source_description="Original source description",
        candidate_suggested_title="Candidate title",
        summary="Summary",
        transcript_excerpt="Excerpt",
    )
    selection_review_user = selection_review.build_user_prompt(
        transcript=[{"start_seconds": 0, "end_seconds": 3, "text": "Hello"}],
        candidates=[
            {
                "candidate_id": "A",
                "start_seconds": 0,
                "end_seconds": 480,
                "suggested_title": "Candidate",
                "selection_reason": "Reason",
                "summary": "Summary",
                "score": 0.97,
            }
        ],
        target_min_duration_seconds=480,
        target_max_duration_seconds=720,
        final_min_duration_seconds=390,
        final_max_duration_seconds=810,
        source_duration_seconds=900,
    )

    assert '"candidate_count":2' in curator_user
    assert "\n" not in curator_user
    assert "\n" not in translation_user
    assert "\n" not in metadata_user
    assert "\n" not in selection_review_user
    curator_payload = json.loads(curator_user)
    assert curator.PROMPT_VERSION == "curator-v6"
    assert selection_review.PROMPT_VERSION == "selection-review-v1"
    assert "do not write timestamp ranges" in selection_review_user
    assert "translate every source item" in translation.SYSTEM_PROMPT.lower()
    assert "filler" in translation.SYSTEM_PROMPT.lower()
    assert "never summarize" in translation.SYSTEM_PROMPT.lower()
    assert "original source timestamps" in curator.SYSTEM_PROMPT
    assert "lowest avoidable waste" in curator.SYSTEM_PROMPT
    assert curator_payload["topics"][0]["topic_id"] == "T1"
    assert curator_payload["source_duration_seconds"] is None
    assert "highest useful knowledge density" in curator_payload["long_form_clip_goal"]
    assert (
        curator_payload["transcript_scope"]
        == "selected_source_windows_around_ranked_topics"
    )
    assert curator_payload["transcript_is_complete"] is False
    assert curator_payload["selection_window_plan"] == []
    assert curator_payload["selection_hints"] == []
    assert curator_payload["original_segment_count"] is None
    assert curator_payload["provided_segment_count"] is None
    assert curator_payload["selection_priority"] == [
        "complete_argument_with_clear_payoff",
        "audience_relevance",
        "high_information_density_across_the_full_8_to_12_minutes",
        "low_host_banter_repetition_and_tangents",
        "necessary_context_without_overlong_setup",
        "duration_fit",
    ]
    assert curator_payload["require_distinct_topics"] is True
    assert curator_payload["required_arc"] == [
        "necessary_background",
        "central_claim_or_finding",
        "key_evidence_or_reasoning",
        "meaningful_conclusion",
    ]
    assert curator_payload["target_min_duration_seconds"] == 480
    assert curator_payload["target_max_duration_seconds"] == 720
    assert curator_payload["accepted_min_duration_seconds"] == 420
    assert curator_payload["accepted_max_duration_seconds"] == 780
    assert curator_payload["final_min_duration_seconds"] == 390
    assert curator_payload["final_max_duration_seconds"] == 810
    assert curator_payload["times_are_approximate"] is True
    assert "aim for the target range" in curator_payload["duration_instruction"].lower()
    assert "complete argument" in curator_payload["duration_instruction"].lower()
    assert "segment alignment" in curator_payload["duration_instruction"].lower()
    assert "low-value material" in curator_payload["duration_instruction"].lower()
    assert "host_reactions_without_new_meaning" in json.dumps(
        curator_payload["waste_ratio_guidance"],
        ensure_ascii=False,
    )
    assert "anecdotes as evidence" in curator_payload["story_policy"]
    assert curator_payload["candidate_quality_bar"] == [
        "clear_standalone_viewer_payoff",
        "specific_insight_or_tension",
        "enough_context_without_long_setup",
        "evidence_or_reasoning_inside_the_clip",
        "low_waste_ratio_for_an_8_to_12_minute_clip",
        "minimal_overlap_with_other_candidates",
        "defensible_title_and_summary",
    ]
    assert curator_payload["structured_candidate_package"]["core_claim"]
    assert curator_payload["structured_candidate_package"]["payoff"]
    assert curator_payload["structured_candidate_package"]["argument_arc"] == [
        "necessary setup",
        "central claim",
        "key evidence or reasoning",
        "conclusion or transition",
    ]
    assert (
        curator_payload["structured_candidate_package"]["boundary_ending_type"]
        == "conclusion | transition | unresolved | cutoff_risk"
    )
    assert curator_payload["selection_reason_requirements"] == [
        "state_the_core_audience_payoff",
        "explain_the_central_claim_or_framework",
        "explain_why_the_full_range_has_low_avoidable_waste",
        "identify_how_examples_or_stories_support_the_lesson",
        "avoid_selecting_a_clip_primarily_because_the_story_is_entertaining",
    ]
    assert curator_payload["overlap_policy"] == (
        "Prefer non-overlapping candidates. Only reuse source time when the second "
        "candidate explains a materially different idea and the overlap is necessary."
    )
    selection_review_payload = json.loads(selection_review_user)
    assert selection_review_payload["candidates"][0]["candidate_id"] == "A"
    assert "boundary excerpts only" in selection_review_payload["transcript_scope"]
    assert any(
        "Do not stop at 8 minutes" in rule
        for rule in selection_review_payload["decision_rules"]
    )
    assert "ending_completeness" in selection_review_payload["review_each_candidate_for"]
    assert "rank 1" in selection_review_payload["goal"]
    assert '"segment_id":"s1"' in translation_user
    translation_payload = json.loads(translation_user)
    assert translation.PROMPT_VERSION == "translation-v3"
    assert translation_payload["item_count"] == 1
    assert translation_payload["required_segment_ids"] == ["s1"]
    assert translation_payload["mapping_contract"] == {
        "preserve_segment_ids_exactly": True,
        "preserve_item_order": True,
        "return_exactly_one_translation_per_source_item": True,
        "do_not_merge_split_omit_add_or_reorder_items": True,
        "translated_text_must_be_readable_traditional_chinese": True,
    }
    assert '"source_title":"Source"' in metadata_user


def test_prompt_serialization_uses_compact_json_and_short_transcript_fields() -> None:
    assert compact_json({"b": 1, "a": ["x", "y"]}) == '{"b":1,"a":["x","y"]}'

    payload = serialize_transcript_segments_for_prompt(
        [
            TranscriptSegment(
                segment_id="0-12",
                start_seconds=12.3456,
                end_seconds=18.9876,
                text="  Useful idea  ",
            )
        ]
    )

    assert payload == [
        {
            "id": "0-12",
            "start": 12.346,
            "end": 18.988,
            "text": "Useful idea",
        }
    ]


def test_topic_discovery_prompt_ranks_distinct_important_topics() -> None:
    user_prompt = topic_discovery.build_user_prompt(
        transcript=[{"start_seconds": 0, "end_seconds": 3, "text": "Hello"}],
        topic_pool_size=4,
        validation_feedback=None,
    )

    payload = json.loads(user_prompt)
    assert topic_discovery.PROMPT_VERSION == "topic-discovery-v3"
    assert payload["topic_pool_size"] == 4
    assert payload["evaluate_full_transcript"] is True
    assert payload["transcript_scope"] == "full_transcript"
    assert payload["transcript_is_complete"] is True
    assert payload["window_plan"] == []
    assert payload["rank_by_importance"] is True
    assert payload["require_distinct_topics"] is True
    assert payload["exclude_low_value_material"] == [
        "greetings",
        "sponsorships",
        "repetition",
        "anecdotes_without_a_broader_point",
        "setup_without_a_conclusion",
    ]
    assert payload["evaluation_rubric"] == [
        "importance_to_the_source_argument",
        "standalone_clip_potential",
        "audience_relevance_for_traditional_chinese_viewers",
        "specific_or_counterintuitive_insight",
        "evidence_density",
        "evergreen_value",
        "low_context_dependency",
    ]
    assert payload["ranking_instruction"] == (
        "Rank topics by expected InsightCast highlight value, not by emotional intensity "
        "or how early the idea appears in the source."
    )
    system_prompt = topic_discovery.SYSTEM_PROMPT.lower()
    assert "full transcript" in system_prompt
    assert "original source timestamps" in system_prompt
    assert "importance" in system_prompt
    assert "standalone" in system_prompt
    assert "distinct" in system_prompt
    assert "controvers" in system_prompt


def test_metadata_prompt_uses_grounded_knowledge_news_framing() -> None:
    prompt = metadata.build_user_prompt(
        source_title="Foreign source",
        source_description="Original source description",
        candidate_suggested_title="Candidate title",
        summary="A supported central finding",
        transcript_excerpt="Evidence and conclusion",
        candidate_core_claim="The job is closer to software engineering than ML research.",
        candidate_payoff="Viewers can decide whether AI engineering matches their skills.",
        candidate_argument_arc=[
            "AI roles are popular",
            "AI engineers mostly integrate foundation models",
            "the role pays well because product delivery is valuable",
        ],
        candidate_boundary_notes={
            "start": "Opens by defining the role.",
            "end": "Ends before the hiring-market caveats.",
        },
    )
    payload = json.loads(prompt)
    system = metadata.SYSTEM_PROMPT.lower()

    assert metadata.PROMPT_VERSION == "metadata-v15"
    assert "traditional chinese" in system
    assert "youtube metadata" in system
    assert "knowledge highlight" in system
    assert "fixed insightcast disclosure" in system
    assert "insightcast" in system
    assert "brand voice" in system
    assert "viewer outcome" in system
    assert "unsupported" in system
    assert "source_equity_hook" in system
    assert "audience_pain_reframe" in system
    assert "<narrative topic>：<sub narrative>" in metadata.SYSTEM_PROMPT
    assert "vertical bar" in system
    assert "speaker" in system
    assert "one-shot" in system
    assert "source_equity" in system
    assert "pain_point" in system
    assert "mechanism" in system
    assert "forbidden_overreach" in system
    assert "鬼故事" in metadata.SYSTEM_PROMPT
    assert payload["candidate_suggested_title"] == "Candidate title"
    assert payload["candidate_editorial_package"] == {
        "core_claim": "The job is closer to software engineering than ML research.",
        "payoff": "Viewers can decide whether AI engineering matches their skills.",
        "argument_arc": [
            "AI roles are popular",
            "AI engineers mostly integrate foundation models",
            "the role pays well because product delivery is valuable",
        ],
        "boundary_notes": {
            "start": "Opens by defining the role.",
            "end": "Ends before the hiring-market caveats.",
        },
    }
    assert payload["source_description_excerpt"] == "Original source description"
    assert set(payload) == {
        "source_title",
        "source_description_excerpt",
        "candidate_suggested_title",
        "candidate_editorial_package",
        "summary",
        "transcript_excerpt",
    }


def test_metadata_prompt_preserves_source_title_equity_for_highlight_metadata() -> None:
    prompt = metadata.build_user_prompt(
        source_title="How to Speak",
        source_description=(
            "This source explains public speaking, contribution, and memorable endings."
        ),
        candidate_suggested_title="How vision makes a talk memorable",
        summary="The segment explains how vision and contribution make talks persuasive.",
        transcript_excerpt="Vision, contribution, and a strong ending make a talk memorable.",
    )
    payload = json.loads(prompt)
    system = metadata.SYSTEM_PROMPT.lower()

    assert metadata.PROMPT_VERSION == "metadata-v15"
    assert "source title" in system
    assert "highlight" in system
    assert "traditional chinese title" in system
    assert "should lead" in system
    assert "packaging editor" in system
    assert "source equity" in system
    assert "original-title tension" in system
    assert "do not generate speaker" in system
    assert payload["candidate_suggested_title"] == "How vision makes a talk memorable"
    assert (
        payload["source_description_excerpt"]
        == "This source explains public speaking, contribution, and memorable endings."
    )
    assert set(payload) == {
        "source_title",
        "source_description_excerpt",
        "candidate_suggested_title",
        "candidate_editorial_package",
        "summary",
        "transcript_excerpt",
    }
