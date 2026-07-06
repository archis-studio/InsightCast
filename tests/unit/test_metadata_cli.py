import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from insightcast.cli.metadata import run_metadata
from insightcast.domain.models import Candidate, Transcript, TranscriptSegment
from insightcast.engines.publish_engine import GeneratedYouTubeMetadata
from insightcast.infrastructure.ytdlp_client import YouTubeMetadata
from insightcast.storage.file_job_writer import FileJobWriter
from insightcast.storage.manifests import AnalysisState
from insightcast.storage.video_store import VideoStore

VIDEO_ID = "abc123DEF_-"
ANALYSIS_ID = "20260706-010203-abc123"


class FakePublishEngine:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def generate(self, **kwargs: object) -> GeneratedYouTubeMetadata:
        self.calls.append(kwargs)
        Path(kwargs["destination"]).write_text(
            json.dumps(
                {
                    "generated": {
                        "title": "退休規劃的認知誤區：不要把快樂全押在以後",
                        "title_variants": [],
                    },
                    "trace": {"prompt_version": "metadata-test"},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return GeneratedYouTubeMetadata(
            title="退休規劃的認知誤區：不要把快樂全押在以後",
            title_variants=[
                {
                    "title": "退休規劃的認知誤區：不要把快樂全押在以後",
                    "strategy": "source_equity_hook",
                    "rationale": "Uses source equity.",
                },
                {
                    "title": "工作身份的底層機制：退休後少了刺激反而更空",
                    "strategy": "mechanism_breakdown",
                    "rationale": "Explains mechanism.",
                },
                {
                    "title": "提早退休前該想清楚：你失去的可能不只是工作",
                    "strategy": "audience_pain_reframe",
                    "rationale": "Reframes pain.",
                },
            ],
            description="說明",
            tags=["退休"],
        )


def populate(output_dir: Path) -> None:
    writer = FileJobWriter()
    store = VideoStore(output_dir, writer)
    source_metadata = YouTubeMetadata(
        video_id=VIDEO_ID,
        title="The Purpose of Independence",
        description="Morgan Housel discusses work, retirement, and purpose.",
        duration_seconds=1200,
        webpage_url=f"https://www.youtube.com/watch?v={VIDEO_ID}",
    )
    video = store.ensure_video(source_metadata, source_metadata.webpage_url)
    writer.write_json(
        video.root / "source" / "manifest.json",
        {"source_metadata": source_metadata.model_dump(mode="json")},
    )
    transcript = Transcript(
        language="en",
        duration_seconds=1200,
        segments=[
            TranscriptSegment(
                segment_id="s1",
                start_seconds=100,
                end_seconds=130,
                text="Working hard now and enjoying life later can backfire.",
            ),
            TranscriptSegment(
                segment_id="s2",
                start_seconds=400,
                end_seconds=430,
                text="Outside the candidate range.",
            ),
        ],
    )
    writer.write_json(
        video.root / "transcripts" / "tx-123" / "transcript.json",
        transcript,
    )
    candidate = Candidate(
        candidate_id="A",
        start_seconds=90,
        end_seconds=180,
        suggested_title="Why work hard now and enjoy life later can backfire",
        selection_reason="Strong standalone argument.",
        summary="Work can provide identity and purpose, so deferring all enjoyment is risky.",
        core_claim="Deferred enjoyment can leave people empty when work disappears.",
        payoff="Viewers can rethink retirement and ambition.",
        argument_arc=["delayed gratification", "work as identity", "retirement risk"],
        boundary_notes={"start": "starts at the thesis", "end": "ends at conclusion"},
    )
    store.write_analysis(
        video_id=VIDEO_ID,
        analysis_id=ANALYSIS_ID,
        operation_id="job-123",
        created_at=datetime(2026, 7, 6, tzinfo=UTC),
        completed_at=datetime(2026, 7, 6, tzinfo=UTC),
        normalized_source_url=source_metadata.webpage_url,
        transcript_id="tx-123",
        curator_model="curator-test",
        prompt_version="curator-test",
        candidate_count=1,
        min_duration_seconds=60,
        max_duration_seconds=180,
        candidates=[candidate],
        state=AnalysisState.WAITING_SELECTION,
        log_path=Path("logs/job-123.log"),
    )


@pytest.mark.asyncio
async def test_metadata_cli_regenerates_candidate_metadata_from_persisted_artifacts(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output_dir = tmp_path / "outputs"
    populate(output_dir)
    publish = FakePublishEngine()

    exit_code = await run_metadata(
        VIDEO_ID,
        ANALYSIS_ID,
        "A",
        output_dir=output_dir,
        publish_engine=publish,
    )

    assert exit_code == 0
    assert len(publish.calls) == 1
    call = publish.calls[0]
    assert call["candidate_suggested_title"] == (
        "Why work hard now and enjoy life later can backfire"
    )
    assert call["candidate_core_claim"] == (
        "Deferred enjoyment can leave people empty when work disappears."
    )
    assert call["transcript_excerpt"] == (
        "Working hard now and enjoying life later can backfire."
    )
    destination = Path(call["destination"])
    assert destination == (
        output_dir
        / "videos"
        / "abc123DEF_-_the-purpose-of-independence"
        / "analyses"
        / ANALYSIS_ID
        / "candidates"
        / "A"
        / "youtube-metadata.preview.json"
    )
    assert destination.is_file()
    output = capsys.readouterr().out
    assert "Metadata regenerated" in output
    assert "退休規劃的認知誤區：不要把快樂全押在以後" in output
