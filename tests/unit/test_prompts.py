import json

from insightcast.prompts import curator, metadata, translation


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
