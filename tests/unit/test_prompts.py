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
        candidate_count=2,
        target_min_duration_seconds=480,
        target_max_duration_seconds=720,
        accepted_min_duration_seconds=420,
        accepted_max_duration_seconds=780,
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
    assert curator.PROMPT_VERSION == "curator-v2"
    assert curator_payload["target_min_duration_seconds"] == 480
    assert curator_payload["target_max_duration_seconds"] == 720
    assert curator_payload["accepted_min_duration_seconds"] == 420
    assert curator_payload["accepted_max_duration_seconds"] == 780
    assert curator_payload["times_are_approximate"] is True
    assert curator_payload["prefer_complete_idea_arcs"] is True
    assert "aim for the target range" in curator_payload["duration_instruction"].lower()
    assert "fallback" in curator_payload["duration_instruction"].lower()
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
