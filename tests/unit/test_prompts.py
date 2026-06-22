import json

from insightcast.prompts import curator, metadata, topic_discovery, translation


def test_prompt_modules_have_versions_contracts_and_data_only_builders() -> None:
    assert curator.PROMPT_VERSION
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
        summary="Summary",
        transcript_excerpt="Excerpt",
    )

    assert '"candidate_count": 2' in curator_user
    curator_payload = json.loads(curator_user)
    assert curator.PROMPT_VERSION == "curator-v4"
    assert curator_payload["topics"][0]["topic_id"] == "T1"
    assert (
        curator_payload["transcript_scope"]
        == "selected_source_windows_around_ranked_topics"
    )
    assert curator_payload["transcript_is_complete"] is False
    assert curator_payload["selection_priority"] == [
        "importance",
        "complete_argument",
        "standalone_viewer_value",
        "information_density",
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
    assert curator_payload["candidate_quality_bar"] == [
        "clear_standalone_viewer_payoff",
        "specific_insight_or_tension",
        "enough_context_without_long_setup",
        "evidence_or_reasoning_inside_the_clip",
        "minimal_overlap_with_other_candidates",
        "defensible_title_and_summary",
    ]
    assert curator_payload["overlap_policy"] == (
        "Prefer non-overlapping candidates. Only reuse source time when the second "
        "candidate explains a materially different idea and the overlap is necessary."
    )
    assert '"segment_id": "s1"' in translation_user
    assert '"source_title": "Source"' in metadata_user


def test_topic_discovery_prompt_ranks_distinct_important_topics() -> None:
    user_prompt = topic_discovery.build_user_prompt(
        transcript=[{"start_seconds": 0, "end_seconds": 3, "text": "Hello"}],
        topic_pool_size=4,
        validation_feedback=None,
    )

    payload = json.loads(user_prompt)
    assert topic_discovery.PROMPT_VERSION == "topic-discovery-v2"
    assert payload["topic_pool_size"] == 4
    assert payload["evaluate_full_transcript"] is True
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
    assert "importance" in system_prompt
    assert "standalone" in system_prompt
    assert "distinct" in system_prompt
    assert "controvers" in system_prompt


def test_metadata_prompt_uses_grounded_knowledge_news_framing() -> None:
    prompt = metadata.build_user_prompt(
        source_title="Foreign source",
        summary="A supported central finding",
        transcript_excerpt="Evidence and conclusion",
    )
    payload = json.loads(prompt)
    system = metadata.SYSTEM_PROMPT.lower()

    assert metadata.PROMPT_VERSION == "metadata-v5"
    assert "traditional chinese" in system
    assert "youtube metadata" in system
    assert "translated highlight" in system
    assert "original video" in system
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
        "lead_with_a_specific_idea_risk_gain_or_tension",
        "make_the_viewer_feel_the_practical_stakes",
        "keep_one_clear_hook_without_clickbait",
        "use_one_source_anchor_for_trust_when_helpful",
    ]
    assert payload["title_frame_options"] == [
        "focal_point_colon_narrative",
        "risk_or_cost_warning",
        "benefit_or_capability_gain",
        "counterintuitive_claim",
        "specific_question",
        "source_anchor_plus_clip_value",
    ]
    assert payload["title_diversity_guidance"] == [
        "do_not_force_every_video_into_the_same_structure",
        "vary_rhythm_between_colon_question_warning_and_direct_claim_when_supported",
        "generic_framing_like_這段影片_or_作者說_is_allowed_only_when_it_sounds_natural",
        "avoid_machine_translated_symmetry_or_formulaic_parallel_phrasing",
    ]
    assert payload["description_strategy"] == [
        "opening_hook_for_target_viewer",
        "why_this_clip_matters_now",
        "what_the_viewer_will_understand_after_watching",
        "key_reasoning_or_examples_from_the_segment",
        "traditional_chinese_translated_highlight_disclosure",
        "original_video_attribution_for_full_context",
    ]
    assert payload["title_quality_bar"] == [
        "specific_enough_to_stand_without_the_original_title",
        "aim_for_50_to_70_readable_characters_under_youtube_100_character_limit",
        "audience_can_sense_what_to_gain_or_avoid",
        "fresh_and_human_not_template_repeated",
        "no_unsupported_superlatives_or_guarantees",
    ]


def test_metadata_prompt_preserves_source_title_equity_for_highlight_metadata() -> None:
    prompt = metadata.build_user_prompt(
        source_title="How to Speak",
        summary="The segment explains how vision and contribution make talks persuasive.",
        transcript_excerpt="Vision, contribution, and a strong ending make a talk memorable.",
    )
    payload = json.loads(prompt)
    system = metadata.SYSTEM_PROMPT.lower()

    assert metadata.PROMPT_VERSION == "metadata-v5"
    assert "source title" in system
    assert "highlight" in system
    assert "traditional chinese title" in system
    assert "should lead" in system
    assert "packaging editor" in system
    assert payload["source_title_retention_strategy"] == [
        "preserve_one_recognizable_source_title_element_when_it_adds_trust",
        "do_not_force_the_full_original_title",
        "do_not_let_source_title_overpower_the_clip_value",
        "blend_source_anchor_with_selected_highlight_focus",
    ]
    assert payload["highlight_positioning"] == (
        "Package the selected segment as a standalone InsightCast knowledge highlight "
        "for Traditional Chinese viewers, not as a replacement for the full original video."
    )
    assert payload["title_style_examples"] == [
        "AI 認知外包：你省下時間，也可能交出判斷力",
        "為什麼你越用 AI 越不會思考？MIT 研究給了一個警訊",
        "別再只改 Prompt：Karpathy 的三層 AI 工作法",
    ]
    assert payload["description_structure"] == [
        "one_sentence_hook",
        "2_to_3_short_paragraphs_or_compact_bullets",
        "clear_original_source_attribution",
        "traditional_chinese_highlight_disclosure",
    ]
