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
    assert curator.PROMPT_VERSION == "curator-v3"
    assert curator_payload["topics"][0]["topic_id"] == "T1"
    assert (
        curator_payload["transcript_scope"]
        == "selected_source_windows_around_ranked_topics"
    )
    assert curator_payload["transcript_is_complete"] is False
    assert curator_payload["selection_priority"] == [
        "importance",
        "complete_argument",
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
    assert '"segment_id": "s1"' in translation_user
    assert '"source_title": "Source"' in metadata_user


def test_topic_discovery_prompt_ranks_distinct_important_topics() -> None:
    user_prompt = topic_discovery.build_user_prompt(
        transcript=[{"start_seconds": 0, "end_seconds": 3, "text": "Hello"}],
        topic_pool_size=4,
        validation_feedback=None,
    )

    payload = json.loads(user_prompt)
    assert topic_discovery.PROMPT_VERSION == "topic-discovery-v1"
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
    system_prompt = topic_discovery.SYSTEM_PROMPT.lower()
    assert "full transcript" in system_prompt
    assert "importance" in system_prompt
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

    assert metadata.PROMPT_VERSION == "metadata-v3"
    assert "traditional chinese" in system
    assert "youtube metadata" in system
    assert "translated highlight" in system
    assert "original video" in system
    assert "unsupported" in system
    assert payload["title_strategy"] == [
        "traditional_chinese_viewer_value_first",
        "selected_highlight_focus",
        "source_title_element_for_trust_search_or_attribution",
        "grounded_curiosity_without_clickbait",
    ]
    assert payload["description_strategy"] == [
        "why_the_topic_matters",
        "central_claim_and_supporting_reasoning",
        "necessary_context",
        "traditional_chinese_translated_highlight_disclosure",
        "consult_original_video_for_full_discussion",
    ]


def test_metadata_prompt_preserves_source_title_equity_for_highlight_metadata() -> None:
    prompt = metadata.build_user_prompt(
        source_title="How to Speak",
        summary="The segment explains how vision and contribution make talks persuasive.",
        transcript_excerpt="Vision, contribution, and a strong ending make a talk memorable.",
    )
    payload = json.loads(prompt)
    system = metadata.SYSTEM_PROMPT.lower()

    assert metadata.PROMPT_VERSION == "metadata-v3"
    assert "source title" in system
    assert "highlight" in system
    assert "traditional chinese title" in system
    assert "should lead" in system
    assert payload["source_title_retention_strategy"] == [
        "preserve_one_recognizable_source_title_element",
        "do_not_force_the_full_original_title",
        "blend_source_title_element_with_selected_highlight_focus",
    ]
    assert payload["highlight_positioning"] == (
        "Package the selected segment as a valuable Traditional Chinese highlight, "
        "not as a replacement for the full original video."
    )
    assert payload["title_style_examples"] == [
        "如何讓演講更有說服力？《How to Speak》精華：Vision、Contribution 與強收尾",
        "AI 如何改變工作？《原標題關鍵詞》精華：最值得重看的 12 分鐘",
    ]
