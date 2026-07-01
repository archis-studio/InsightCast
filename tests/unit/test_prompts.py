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
    assert translation.PROMPT_VERSION == "translation-v2"
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
    )
    payload = json.loads(prompt)
    system = metadata.SYSTEM_PROMPT.lower()

    assert metadata.PROMPT_VERSION == "metadata-v10"
    assert "traditional chinese" in system
    assert "youtube metadata" in system
    assert "knowledge highlight" in system
    assert "fixed insightcast disclosure" in system
    assert "insightcast" in system
    assert "brand voice" in system
    assert "viewer outcome" in system
    assert "unsupported" in system
    assert payload["brand_positioning"] == {
        "product": "InsightCast",
        "promise": (
            "help Traditional Chinese viewers quickly decide why this foreign-language "
            "knowledge highlight is worth their attention"
        ),
        "voice": [
            "editorial",
            "precise",
            "premium_but_plainspoken",
            "curious_without_hype",
        ],
    }
    assert payload["title_strategy"] == [
        "choose_the_title_frame_that_best_fits_the_clip",
        "prefer_flexible_anchor_colon_narrative_structure_when_natural",
        "do_not_add_author_host_or_guest_names_by_default",
        "use_candidate_suggested_title_as_the_segment_semantic_center",
        "use_source_title_and_description_as_context_boundaries",
        "lead_with_a_specific_idea_risk_gain_or_tension",
        "make_the_viewer_feel_the_practical_stakes",
        "keep_one_clear_hook_without_clickbait",
        "use_one_source_anchor_for_trust_when_helpful",
    ]
    assert payload["title_variant_requirements"]["variant_count"] == 3
    assert payload["title_variant_requirements"]["primary_title_must_match_one_variant"]
    assert payload["title_variant_requirements"]["preferred_structure"] == (
        "<anchor_or_topic_narrative>：<argument_or_payoff>"
    )
    assert [
        item["strategy"] for item in payload["title_variant_requirements"]["strategies"]
    ] == ["macro_reframe", "mechanism", "audience_payoff"]
    assert payload["title_variant_requirements"]["choose_primary_by"] == [
        "truthfulness_to_segment",
        "anchor_colon_narrative_readability",
        "viewer_payoff_clarity",
        "source_context_alignment_without_author_name",
        "traditional_chinese_youtube_readability",
    ]
    assert payload["candidate_suggested_title"] == "Candidate title"
    assert payload["source_description_excerpt"] == "Original source description"
    assert payload["title_alignment_contract"] == {
        "must_reflect_candidate_segment": True,
        "must_not_drift_beyond_source_description_context": True,
        "should_preserve_source_title_promise_or_tension_when_relevant": True,
        "should_preserve_candidate_suggested_title_meaning": True,
        "may_rewrite_for_traditional_chinese_youtube_packaging": True,
        "must_not_overpromise_beyond_summary_or_transcript": True,
    }
    assert payload["title_frame_options"] == [
        "flexible_anchor_colon_narrative",
        "macro_or_strategic_reframe",
        "risk_or_cost_warning",
        "benefit_or_capability_gain",
        "counterintuitive_claim",
        "specific_question",
        "source_anchor_plus_clip_value",
    ]
    assert payload["title_diversity_guidance"] == [
        "do_not_force_every_video_into_the_same_structure",
        "the_anchor_before_the_colon_can_be_a_topic_or_short_thematic_setup",
        "vary_rhythm_between_colon_question_warning_and_direct_claim_when_supported",
        "generic_framing_like_這段影片_or_作者說_is_allowed_only_when_it_sounds_natural",
        "avoid_machine_translated_symmetry_or_formulaic_parallel_phrasing",
        "avoid_channel_titles_feeling_like_the_same_template_repeated",
    ]
    assert payload["description_strategy"] == [
        "opening_hook_for_target_viewer",
        "why_this_clip_matters_now",
        "what_the_viewer_will_understand_after_watching",
        "key_reasoning_or_examples_from_the_segment",
        "no_insightcast_branding_in_description_body",
        "fixed_insightcast_disclaimer_is_appended_after_generation",
    ]
    assert payload["title_quality_bar"] == [
        "specific_enough_to_stand_without_the_original_title",
        "aim_for_50_to_70_readable_characters_under_youtube_100_character_limit",
        "audience_can_sense_what_to_gain_or_avoid",
        "calm_neutral_editorial_tone_with_tension_but_without_hype",
        "fresh_and_human_not_template_repeated",
        "no_unsupported_superlatives_or_guarantees",
    ]


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

    assert metadata.PROMPT_VERSION == "metadata-v10"
    assert "source title" in system
    assert "highlight" in system
    assert "traditional chinese title" in system
    assert "should lead" in system
    assert "packaging editor" in system
    assert payload["source_title_retention_strategy"] == [
        "preserve_one_recognizable_source_title_element_when_it_adds_trust",
        "do_not_add_author_host_or_guest_names_by_default",
        "do_not_force_the_full_original_title",
        "do_not_let_source_title_overpower_the_clip_value",
        "blend_source_anchor_with_selected_highlight_focus",
    ]
    assert payload["candidate_suggested_title"] == "How vision makes a talk memorable"
    assert (
        payload["source_description_excerpt"]
        == "This source explains public speaking, contribution, and memorable endings."
    )
    assert payload["highlight_positioning"] == (
        "Package the selected segment as a standalone InsightCast knowledge highlight "
        "for Traditional Chinese viewers, not as a replacement for the full original video."
    )
    assert payload["title_style_examples"] == [
        "全球金融正在換規則：為什麼紙面合約不再代表真正的黃金市場",
        "黃金市場：拆解紙黃金、槓桿交易與真實供需的定價權轉移",
        "AI 代理不只是省人力：語音正在變成下一代操作介面",
        "你以為的分散投資：S&P 500 其實可能押在少數科技巨頭上",
        "學程式別急著用 AI：先看懂這個理解錯覺",
    ]
    assert payload["description_structure"] == [
        "single_compact_paragraph",
        "no_newline_characters",
        "no_bullets",
        "no_manual_insightcast_disclosure",
    ]
