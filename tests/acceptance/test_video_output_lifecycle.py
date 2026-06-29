from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from insightcast.domain.models import (
    Candidate,
    CandidateSelectionRequest,
    SourceArtifacts,
    Transcript,
    TranscriptSegment,
)
from insightcast.engines.clip_engine import ClipArtifacts
from insightcast.engines.curator_engine import (
    CurationResult,
    TopicDiscoveryOutput,
    TopicDiscoveryResponse,
)
from insightcast.engines.lingo_engine import SubtitleItem
from insightcast.engines.publish_engine import GeneratedYouTubeMetadata
from insightcast.engines.source_engine import SourceResult
from insightcast.infrastructure.ytdlp_client import YouTubeMetadata
from insightcast.services.job_service import JobService
from insightcast.storage.file_job_writer import FileJobWriter
from insightcast.storage.video_store import VideoStore

VIDEO_ID = "abc123DEF_-"
WATCH_URL = f"https://www.youtube.com/watch?v={VIDEO_ID}"
SHARE_URL = f"https://youtu.be/{VIDEO_ID}"


class Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 6, 7, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        current = self.value
        self.value += timedelta(seconds=1)
        return current


class IdFactory:
    def __init__(self) -> None:
        self.value = 0

    def __call__(self) -> str:
        self.value += 1
        return f"id{self.value:04d}"


class FakeSource:
    def __init__(self) -> None:
        self.download_count = 0

    async def ingest(self, **kwargs: object) -> SourceResult:
        store = VideoStore(Path(kwargs["output_root"]), FileJobWriter())
        metadata = YouTubeMetadata(
            video_id=VIDEO_ID,
            title="Acceptance Source",
            duration_seconds=600,
            webpage_url=str(kwargs["youtube_url"]),
        )
        async with store.source_transaction(VIDEO_ID) as transaction:
            lookup = transaction.load_source()
            if lookup.entry is None:
                self.download_count += 1
                transaction.ensure_video(metadata, str(kwargs["youtube_url"]))
                staging = transaction.create_staging()
                (staging / "source.mp4").write_bytes(b"video")
                (staging / "audio.mp3").write_bytes(b"audio")
                source = transaction.promote(
                    staging,
                    metadata=metadata,
                    downloaded_at=datetime(2026, 6, 7, 12, 0, tzinfo=UTC),
                    audio_extracted_at=datetime(2026, 6, 7, 12, 1, tzinfo=UTC),
                )
                cache_decision = "miss"
            else:
                source = lookup.entry
                cache_decision = "hit"
        return SourceResult(
            output_dir=source.root,
            metadata=metadata,
            source_artifacts=SourceArtifacts(
                source_video=source.source_video,
                source_audio=source.source_audio,
            ),
            cache_decision=cache_decision,
        )


class FakeTranscriber:
    transcription_provider = "openai"
    transcription_model = "whisper-1"
    transcription_language = "en"
    transcript_schema_version = 1

    def __init__(self) -> None:
        self.call_count = 0

    async def transcribe(self, _path: Path) -> Transcript:
        self.call_count += 1
        return Transcript(
            language="en",
            duration_seconds=600,
            segments=[
                TranscriptSegment(
                    segment_id="s1",
                    start_seconds=0,
                    end_seconds=600,
                    text="Acceptance transcript",
                )
            ],
        )


class FakeCurator:
    async def discover_topics(self, **_kwargs: object) -> TopicDiscoveryResponse:
        return TopicDiscoveryResponse(
            topics=[
                TopicDiscoveryOutput(
                    topic_id="T1",
                    label="Acceptance topic",
                    summary="Acceptance topic summary.",
                    central_claim="Acceptance central claim.",
                    importance_reason="Acceptance importance reason.",
                    start_seconds=0,
                    end_seconds=600,
                    importance_score=0.95,
                ),
                TopicDiscoveryOutput(
                    topic_id="T2",
                    label="Alternate acceptance topic",
                    summary="Alternate acceptance topic summary.",
                    central_claim="Alternate acceptance central claim.",
                    importance_reason="Alternate acceptance importance reason.",
                    start_seconds=0,
                    end_seconds=600,
                    importance_score=0.90,
                ),
            ]
        )

    async def select_candidates(self, **kwargs: object) -> CurationResult:
        assert isinstance(kwargs["topics"], TopicDiscoveryResponse)
        return CurationResult(
            candidates=[
                Candidate(
                    candidate_id="A",
                    start_seconds=0,
                    end_seconds=600,
                    suggested_title="Candidate A",
                    selection_reason="Complete standalone segment.",
                    summary="Acceptance summary.",
                )
            ],
            model="gpt-curator",
            prompt_version="topic-discovery-v2+curator-v4",
        )


class FakeClip:
    async def cut_clip(
        self,
        _source_video: Path,
        selection: Candidate,
        work_dir: Path,
    ) -> Path:
        work_dir.mkdir(parents=True, exist_ok=True)
        temporary_clip = work_dir / f"{selection.candidate_id}.unburned.mp4"
        temporary_clip.write_bytes(b"temporary")
        return temporary_clip

    async def translate_subtitles(
        self,
        transcript_segments: list[TranscriptSegment],
        selection: Candidate,
    ) -> list[SubtitleItem]:
        return [
            SubtitleItem(
                segment_id=segment.segment_id,
                start_seconds=max(segment.start_seconds, selection.start_seconds)
                - selection.start_seconds,
                end_seconds=min(segment.end_seconds, selection.end_seconds)
                - selection.start_seconds,
                english_text=segment.text,
                traditional_chinese_text="翻譯",
            )
            for segment in transcript_segments
            if segment.end_seconds > selection.start_seconds
            and segment.start_seconds < selection.end_seconds
        ]

    def write_subtitles(
        self,
        _subtitle_items: list[SubtitleItem],
        _selection: Candidate,
        output_dir: Path,
    ) -> tuple[Path, Path]:
        output_dir.mkdir(parents=True, exist_ok=True)
        srt = output_dir / "subtitles.zh-TW.srt"
        ass = output_dir / "subtitles.bilingual.ass"
        srt.write_text("srt", encoding="utf-8")
        ass.write_text("ass", encoding="utf-8")
        return srt, ass

    async def burn_subtitles(
        self,
        _temporary_clip: Path,
        _ass_path: Path,
        output_dir: Path,
    ) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        burned = output_dir / "video.mp4"
        burned.write_bytes(b"video")
        return burned

    async def render(self, **kwargs: object) -> ClipArtifacts:
        output_dir = Path(kwargs["output_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)
        traditional_chinese_srt = output_dir / "subtitles.zh-TW.srt"
        bilingual_ass = output_dir / "subtitles.bilingual.ass"
        burned_video = output_dir / "video.mp4"
        traditional_chinese_srt.write_text("srt", encoding="utf-8")
        bilingual_ass.write_text("ass", encoding="utf-8")
        burned_video.write_bytes(b"video")
        return ClipArtifacts(
            traditional_chinese_srt=traditional_chinese_srt,
            bilingual_ass=bilingual_ass,
            burned_video=burned_video,
        )


class FakePublish:
    async def generate(self, **kwargs: object) -> GeneratedYouTubeMetadata:
        Path(kwargs["destination"]).write_text("{}", encoding="utf-8")
        return GeneratedYouTubeMetadata(
            title="Acceptance title",
            title_variants=[
                {
                    "title": "Acceptance title",
                    "strategy": "conceptual_reframe",
                    "rationale": "Conceptual reframe.",
                },
                {
                    "title": "Acceptance pain title",
                    "strategy": "pain_point",
                    "rationale": "Pain point.",
                },
                {
                    "title": "Acceptance mechanism title",
                    "strategy": "mechanism",
                    "rationale": "Mechanism.",
                },
                {
                    "title": "Acceptance clean hook title",
                    "strategy": "clean_hook",
                    "rationale": "Clean hook.",
                },
            ],
            description="Acceptance description",
            tags=["acceptance"],
        )


def make_acceptance_service(
    tmp_path: Path,
) -> tuple[JobService, FakeSource, FakeTranscriber]:
    source = FakeSource()
    transcriber = FakeTranscriber()
    service = JobService(
        output_root=tmp_path / "outputs",
        work_root=tmp_path / ".work",
        source_engine=source,
        transcription_client=transcriber,
        curator_engine=FakeCurator(),
        clip_engine=FakeClip(),
        publish_engine=FakePublish(),
        writer=FileJobWriter(),
        clock=Clock(),
        id_factory=IdFactory(),
    )
    return service, source, transcriber


async def analyze(
    service: JobService,
    url: str,
    *,
    force_reanalyze: bool = False,
):
    job = await service.create_analysis_job(
        url,
        force_reanalyze=force_reanalyze,
    )
    await service.process(await service.queue.get())
    return job


async def render(service: JobService, job, candidate_id: str, *, force: bool):
    batch = await service.create_render(
        job.job_id,
        CandidateSelectionRequest(
            candidate_ids=candidate_id,
            force_render=force,
        ),
    )
    await service.process(await service.queue.get())
    return batch


@pytest.mark.asyncio
async def test_video_output_lifecycle_survives_fresh_store_instance(
    tmp_path: Path,
) -> None:
    service, source, transcriber = make_acceptance_service(tmp_path)
    first = await analyze(service, WATCH_URL)
    second = await analyze(service, SHARE_URL, force_reanalyze=True)
    render_one = await render(service, first, "A", force=True)
    render_two = await render(service, first, "A", force=True)

    assert source.download_count == 1
    assert transcriber.call_count == 1
    assert first.analysis_id != second.analysis_id
    assert render_one.render_id != render_two.render_id

    legacy = tmp_path / "outputs" / "20260606-legacy-job"
    legacy.mkdir(parents=True)
    (legacy / "job_state.json").write_text("{}", encoding="utf-8")

    fresh = VideoStore(tmp_path / "outputs", FileJobWriter())
    renders = fresh.list_publishable_renders(VIDEO_ID)
    selected = fresh.resolve_publishable_render(VIDEO_ID, render_two.render_id)

    assert {item.manifest.render_id for item in renders} >= {
        render_one.render_id,
        render_two.render_id,
    }
    assert selected.manifest.candidate_id == "A"
    assert selected.artifacts is not None
    assert selected.artifacts.burned_video.name == "video.mp4"
    assert fresh.list_analyses(VIDEO_ID)
    assert legacy.exists()
