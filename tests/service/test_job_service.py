import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from insightcast.core.exceptions import InsightCastError
from insightcast.core.logging import get_job_log_path
from insightcast.domain.enums import ErrorCode, JobStatus
from insightcast.domain.models import (
    Candidate,
    CandidateSelectionRequest,
    RenderArtifacts,
    SourceArtifacts,
    Transcript,
    TranscriptSegment,
)
from insightcast.domain.stages import StageManifest
from insightcast.engines.clip_engine import ClipArtifacts
from insightcast.engines.curator_engine import (
    CurationResult,
    TopicDiscoveryOutput,
    TopicDiscoveryResponse,
)
from insightcast.engines.lingo_engine import SubtitleItem
from insightcast.engines.publish_engine import GeneratedYouTubeMetadata
from insightcast.engines.source_engine import SourceResult
from insightcast.infrastructure.openai_client import emit_llm_telemetry
from insightcast.infrastructure.transcription.openai_transcription_client import (
    emit_transcription_progress,
)
from insightcast.infrastructure.ytdlp_client import YouTubeMetadata
from insightcast.services.job_service import JobService, WorkKind
from insightcast.storage.file_job_writer import FileJobWriter
from insightcast.storage.manifests import (
    AnalysisManifest,
    AnalysisState,
    RenderManifest,
    RenderState,
)
from insightcast.storage.video_store import VideoStore


class Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 6, 6, 14, 30, tzinfo=UTC)

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


class FakeWriter:
    def __init__(self) -> None:
        self.jobs: list[object] = []
        self.json: list[tuple[Path, object]] = []

    def write_job(self, job: object) -> Path:
        self.jobs.append(job)
        return Path(job.output_dir) / "job_state.json"

    def write_json(self, path: Path, payload: object) -> Path:
        self.json.append((path, payload))
        return path


class FakeSource:
    async def ingest(self, **kwargs: object) -> SourceResult:
        store = VideoStore(Path(kwargs["output_root"]), FileJobWriter())
        metadata = YouTubeMetadata(
            video_id="abc123DEF_-",
            title="Source",
            duration_seconds=1200,
            webpage_url=str(kwargs["youtube_url"]),
        )
        async with store.source_transaction("abc123DEF_-") as transaction:
            lookup = transaction.load_source()
            if lookup.entry is None:
                transaction.ensure_video(metadata, str(kwargs["youtube_url"]))
                staging = transaction.create_staging()
                (staging / "source.mp4").write_bytes(b"video")
                (staging / "audio.mp3").write_bytes(b"audio")
                source = transaction.promote(
                    staging,
                    metadata=metadata,
                    downloaded_at=datetime(2026, 6, 6, 14, 30, tzinfo=UTC),
                    audio_extracted_at=datetime(2026, 6, 6, 14, 31, tzinfo=UTC),
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
    def __init__(
        self,
        *,
        provider: str = "openai",
        model: str = "whisper-1",
    ) -> None:
        self.transcription_provider = provider
        self.transcription_model = model
        self.transcription_language = "en"
        self.transcript_schema_version = 1
        self.calls: list[Path] = []

    async def transcribe(self, _path: Path) -> Transcript:
        self.calls.append(_path)
        return Transcript(
            language="en",
            duration_seconds=1200,
            segments=[
                TranscriptSegment(
                    segment_id="s1",
                    start_seconds=0,
                    end_seconds=1200,
                    text="Transcript",
                )
            ],
        )


class ProgressFakeTranscriber(FakeTranscriber):
    async def transcribe(self, _path: Path) -> Transcript:
        emit_transcription_progress(
            "planned",
            chunk_count=2,
            max_upload_mb=8,
            total_chunk_bytes=1600,
        )
        emit_transcription_progress(
            "completed",
            chunk_index=0,
            attempt=1,
            processed_chunks=1,
            chunk_count=2,
        )
        emit_transcription_progress(
            "completed_all",
            processed_chunks=2,
            chunk_count=2,
            segment_count=1,
        )
        return await super().transcribe(_path)


def discovered_topic(
    topic_id: str,
    start: float,
    end: float,
    score: float,
) -> TopicDiscoveryOutput:
    return TopicDiscoveryOutput(
        topic_id=topic_id,
        label=f"Topic {topic_id}",
        summary=f"Summary {topic_id}",
        central_claim=f"Claim {topic_id}",
        importance_reason=f"Reason {topic_id}",
        start_seconds=start,
        end_seconds=end,
        importance_score=score,
    )


class FakeCurator:
    def __init__(self) -> None:
        self.discovery_calls = 0
        self.selection_calls = 0

    @property
    def calls(self) -> int:
        return self.discovery_calls + self.selection_calls

    async def discover_topics(self, **_kwargs: object) -> TopicDiscoveryResponse:
        self.discovery_calls += 1
        emit_llm_telemetry(
            {
                "event": "completed",
                "trace_name": "topic_discovery",
                "model": "gpt-curator",
                "response_model": "TopicDiscoveryResponse",
                "attempt": 1,
                "system_chars": 10,
                "user_chars": 20,
                "input_tokens": 5,
                "output_tokens": 3,
                "total_tokens": 8,
            }
        )
        return TopicDiscoveryResponse(
            topics=[
                discovered_topic("T1", 0, 600, 0.95),
                discovered_topic("T2", 600, 1200, 0.90),
                discovered_topic("T3", 0, 600, 0.85),
                discovered_topic("T4", 600, 1200, 0.80),
            ]
        )

    async def select_candidates(self, **kwargs: object) -> CurationResult:
        self.selection_calls += 1
        assert isinstance(kwargs["topics"], TopicDiscoveryResponse)
        return CurationResult(
            candidates=[
                Candidate(
                    candidate_id="A",
                    start_seconds=0,
                    end_seconds=600,
                    suggested_title="A",
                    selection_reason="Complete",
                    summary="Summary A",
                ),
                Candidate(
                    candidate_id="B",
                    start_seconds=600,
                    end_seconds=1200,
                    suggested_title="B",
                    selection_reason="Complete",
                    summary="Summary B",
                ),
            ],
            model="gpt-curator",
            prompt_version="topic-discovery-v2+curator-v4",
        )


class FailingCurator:
    async def discover_topics(self, **_kwargs: object) -> TopicDiscoveryResponse:
        raise InsightCastError(
            ErrorCode.INSUFFICIENT_CANDIDATES,
            "Not enough topics.",
            stage="topic_discovery",
        )

    async def select_candidates(self, **_kwargs: object) -> CurationResult:
        raise AssertionError("selection must not run after discovery fails")


class FakeClip:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.fail_candidates: set[str] = set()
        self.fail_translate_candidates: set[str] = set()

    async def cut_clip(
        self,
        source_video: Path,
        selection: Candidate,
        work_dir: Path,
    ) -> Path:
        self.calls.append(selection.candidate_id)
        work_dir.mkdir(parents=True, exist_ok=True)
        temporary_clip = work_dir / f"{selection.candidate_id}.unburned.mp4"
        temporary_clip.write_bytes(b"temporary")
        return temporary_clip

    async def translate_subtitles(
        self,
        transcript_segments: list[TranscriptSegment],
        selection: Candidate,
    ) -> list[SubtitleItem]:
        if selection.candidate_id in self.fail_translate_candidates:
            raise InsightCastError(
                ErrorCode.LLM_REQUEST_FAILED,
                "llm failed",
                stage="llm",
            )
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
        subtitle_items: list[SubtitleItem],
        selection: Candidate,
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
        temporary_clip: Path,
        ass_path: Path,
        output_dir: Path,
    ) -> Path:
        candidate_id = temporary_clip.name.removesuffix(".unburned.mp4")
        if candidate_id in self.fail_candidates:
            raise InsightCastError(ErrorCode.VIDEO_RENDER_FAILED, "render failed")
        output_dir.mkdir(parents=True, exist_ok=True)
        burned = output_dir / "video.mp4"
        burned.write_bytes(b"video")
        return burned

    async def render(self, **kwargs: object) -> ClipArtifacts:
        selection = kwargs["selection"]
        self.calls.append(selection.candidate_id)
        if selection.candidate_id in self.fail_candidates:
            raise InsightCastError(ErrorCode.VIDEO_RENDER_FAILED, "render failed")
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
            title="Title",
            title_variants=[
                {
                    "title": "Title",
                    "strategy": "conceptual_reframe",
                    "rationale": "Conceptual reframe.",
                },
                {
                    "title": "Pain title",
                    "strategy": "pain_point",
                    "rationale": "Pain point.",
                },
                {
                    "title": "Mechanism title",
                    "strategy": "mechanism",
                    "rationale": "Mechanism.",
                },
                {
                    "title": "Clean hook title",
                    "strategy": "clean_hook",
                    "rationale": "Clean hook.",
                },
            ],
            description="Description",
            tags=["tag"],
        )


class BlockingCutClip(FakeClip):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def cut_clip(
        self,
        source_video: Path,
        selection: Candidate,
        work_dir: Path,
    ) -> Path:
        self.started.set()
        await self.release.wait()
        return await super().cut_clip(source_video, selection, work_dir)


def make_service(tmp_path: Path) -> tuple[JobService, FakeCurator, FakeClip]:
    curator = FakeCurator()
    clip = FakeClip()
    service = JobService(
        output_root=tmp_path / "outputs",
        work_root=tmp_path / ".work",
        source_engine=FakeSource(),
        transcription_client=FakeTranscriber(),
        curator_engine=curator,
        clip_engine=clip,
        publish_engine=FakePublish(),
        writer=FakeWriter(),
        clock=Clock(),
        id_factory=IdFactory(),
    )
    return service, curator, clip


@pytest.mark.asyncio
async def test_forced_analyses_are_immutable_and_write_candidate_directories(
    tmp_path: Path,
) -> None:
    service, _curator, _clip = make_service(tmp_path)

    first = await service.create_analysis_job("https://youtu.be/abc123DEF_-")
    await service.process(await service.queue.get())
    second = await service.create_analysis_job(
        "https://youtu.be/abc123DEF_-",
        force_reanalyze=True,
    )
    await service.process(await service.queue.get())

    assert first.analysis_id != second.analysis_id
    for job in (first, second):
        assert job.video_id == "abc123DEF_-"
        assert job.transcript_id is not None
        assert job.manifest_path == job.output_dir / "manifest.json"
        assert job.output_dir == service.video_store.analysis_dir(
            "abc123DEF_-",
            job.analysis_id,
        )
        assert (job.output_dir / "candidates.json").is_file()
        assert (job.output_dir / "candidates" / "A" / "candidate.json").is_file()
        assert (job.output_dir / "candidates" / "B" / "candidate.json").is_file()

        manifest = AnalysisManifest.model_validate_json(
            (job.output_dir / "manifest.json").read_text(encoding="utf-8")
        )
        assert manifest.state is AnalysisState.WAITING_SELECTION
        assert manifest.video_id == "abc123DEF_-"
        assert manifest.analysis_id == job.analysis_id
        assert manifest.transcript_id == job.transcript_id
        assert manifest.candidates_path == Path(
            f"analyses/{job.analysis_id}/candidates.json"
        )
        assert manifest.candidate_paths == {
            "A": Path(f"analyses/{job.analysis_id}/candidates/A"),
            "B": Path(f"analyses/{job.analysis_id}/candidates/B"),
        }
        assert manifest.log_path == Path(f"logs/{job.job_id}.log")
        log_path = job.output_dir.parent.parent / manifest.log_path
        assert log_path.is_file()
        assert "WAITING_SELECTION" in log_path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_failed_analysis_after_transcript_retains_failed_manifest(
    tmp_path: Path,
) -> None:
    service = JobService(
        output_root=tmp_path / "outputs",
        work_root=tmp_path / ".work",
        source_engine=FakeSource(),
        transcription_client=FakeTranscriber(),
        curator_engine=FailingCurator(),
        clip_engine=FakeClip(),
        publish_engine=FakePublish(),
        writer=FileJobWriter(),
        clock=Clock(),
        id_factory=IdFactory(),
    )
    job = await service.create_analysis_job("https://youtu.be/abc123DEF_-")

    await service.process(await service.queue.get())

    assert job.status is JobStatus.FAILED
    assert job.manifest_path is not None
    manifest = AnalysisManifest.model_validate_json(
        job.manifest_path.read_text(encoding="utf-8")
    )
    assert manifest.state is AnalysisState.FAILED
    assert manifest.error is not None
    assert manifest.error.error_code is ErrorCode.INSUFFICIENT_CANDIDATES
    assert manifest.log_path == Path(f"logs/{job.job_id}.log")


@pytest.mark.asyncio
async def test_failed_analysis_does_not_block_new_analysis_for_same_url(
    tmp_path: Path,
) -> None:
    service = JobService(
        output_root=tmp_path / "outputs",
        work_root=tmp_path / ".work",
        source_engine=FakeSource(),
        transcription_client=FakeTranscriber(),
        curator_engine=FailingCurator(),
        clip_engine=FakeClip(),
        publish_engine=FakePublish(),
        writer=FileJobWriter(),
        clock=Clock(),
        id_factory=IdFactory(),
    )
    first = await service.create_analysis_job("https://youtu.be/abc123DEF_-")

    await service.process(await service.queue.get())
    second = await service.create_analysis_job(
        "https://www.youtube.com/watch?v=abc123DEF_-"
    )

    assert first.status is JobStatus.FAILED
    assert second.job_id != first.job_id
    assert second.status is JobStatus.QUEUED
    assert service.queue.qsize() == 1


@pytest.mark.asyncio
async def test_analysis_reuses_normalized_url_unless_forced(tmp_path: Path) -> None:
    service, _, _ = make_service(tmp_path)

    first = await service.create_analysis_job("https://youtu.be/abc123DEF_-")
    reused = await service.create_analysis_job(
        "https://www.youtube.com/watch?v=abc123DEF_-"
    )
    forced = await service.create_analysis_job(
        "https://youtu.be/abc123DEF_-",
        force_reanalyze=True,
    )

    assert first.job_id == reused.job_id
    assert forced.job_id != first.job_id
    assert service.queue.qsize() == 2


@pytest.mark.asyncio
async def test_analysis_pipeline_stops_at_waiting_selection(tmp_path: Path) -> None:
    service, curator, _ = make_service(tmp_path)
    job = await service.create_analysis_job("https://youtu.be/abc123DEF_-")

    work = await service.queue.get()
    await service.process(work)

    stored = service.get_analysis_job(job.job_id)
    assert stored.status == JobStatus.WAITING_SELECTION
    assert [item.candidate_id for item in stored.candidates] == ["A", "B"]
    assert curator.discovery_calls == 1
    assert curator.selection_calls == 1


@pytest.mark.asyncio
async def test_forced_analysis_reuses_cached_transcript_for_same_source_and_model(
    tmp_path: Path,
) -> None:
    transcriber = FakeTranscriber(model="whisper-1")
    curator = FakeCurator()
    service = JobService(
        output_root=tmp_path / "outputs",
        work_root=tmp_path / ".work",
        source_engine=FakeSource(),
        transcription_client=transcriber,
        curator_engine=curator,
        clip_engine=FakeClip(),
        publish_engine=FakePublish(),
        writer=FakeWriter(),
        clock=Clock(),
        id_factory=IdFactory(),
    )

    first = await service.create_analysis_job("https://youtu.be/abc123DEF_-")
    await service.process(await service.queue.get())
    second = await service.create_analysis_job(
        "https://youtu.be/abc123DEF_-",
        force_reanalyze=True,
    )
    await service.process(await service.queue.get())

    first_artifacts = service.get_analysis_job(first.job_id).source_artifacts
    assert first_artifacts is not None
    assert transcriber.calls == [first_artifacts.source_audio]
    assert service._transcripts[first.job_id] == service._transcripts[second.job_id]
    assert curator.discovery_calls == 2
    assert curator.selection_calls == 2


@pytest.mark.asyncio
async def test_concurrent_forced_analyses_share_same_transcription(
    tmp_path: Path,
) -> None:
    class SlowTranscriber(FakeTranscriber):
        async def transcribe(self, path: Path) -> Transcript:
            self.calls.append(path)
            await asyncio.sleep(0.05)
            return Transcript(
                language="en",
                duration_seconds=1200,
                segments=[
                    TranscriptSegment(
                        segment_id="s1",
                        start_seconds=0,
                        end_seconds=1200,
                        text="Shared Transcript",
                    )
                ],
            )

    transcriber = SlowTranscriber()
    service = JobService(
        output_root=tmp_path / "outputs",
        work_root=tmp_path / ".work",
        source_engine=FakeSource(),
        transcription_client=transcriber,
        curator_engine=FakeCurator(),
        clip_engine=FakeClip(),
        publish_engine=FakePublish(),
        writer=FakeWriter(),
        clock=Clock(),
        id_factory=IdFactory(),
    )
    first = await service.create_analysis_job(
        "https://youtu.be/abc123DEF_-",
        force_reanalyze=True,
    )
    second = await service.create_analysis_job(
        "https://youtu.be/abc123DEF_-",
        force_reanalyze=True,
    )

    first_work = await service.queue.get()
    second_work = await service.queue.get()
    await asyncio.wait_for(
        asyncio.gather(
            service.process(first_work),
            service.process(second_work),
        ),
        timeout=2,
    )

    assert len(transcriber.calls) == 1
    assert service._transcripts[first.job_id] == service._transcripts[second.job_id]
    assert service._transcripts[first.job_id].segments[0].text == "Shared Transcript"


@pytest.mark.asyncio
async def test_analysis_transcribes_again_when_transcription_model_changes(
    tmp_path: Path,
) -> None:
    first_transcriber = FakeTranscriber(model="whisper-1")
    second_transcriber = FakeTranscriber(model="gpt-4o-mini-transcribe")
    service = JobService(
        output_root=tmp_path / "outputs",
        work_root=tmp_path / ".work",
        source_engine=FakeSource(),
        transcription_client=first_transcriber,
        curator_engine=FakeCurator(),
        clip_engine=FakeClip(),
        publish_engine=FakePublish(),
        writer=FakeWriter(),
        clock=Clock(),
        id_factory=IdFactory(),
    )

    await service.create_analysis_job("https://youtu.be/abc123DEF_-")
    await service.process(await service.queue.get())
    service.transcription_client = second_transcriber
    await service.create_analysis_job(
        "https://youtu.be/abc123DEF_-",
        force_reanalyze=True,
    )
    await service.process(await service.queue.get())

    assert len(first_transcriber.calls) == 1
    assert len(second_transcriber.calls) == 1


@pytest.mark.asyncio
async def test_analysis_transcribes_again_when_transcription_provider_changes(
    tmp_path: Path,
) -> None:
    first_transcriber = FakeTranscriber(provider="openai", model="whisper-1")
    second_transcriber = FakeTranscriber(provider="local-whisper", model="whisper-1")
    service = JobService(
        output_root=tmp_path / "outputs",
        work_root=tmp_path / ".work",
        source_engine=FakeSource(),
        transcription_client=first_transcriber,
        curator_engine=FakeCurator(),
        clip_engine=FakeClip(),
        publish_engine=FakePublish(),
        writer=FakeWriter(),
        clock=Clock(),
        id_factory=IdFactory(),
    )

    await service.create_analysis_job("https://youtu.be/abc123DEF_-")
    await service.process(await service.queue.get())
    service.transcription_client = second_transcriber
    await service.create_analysis_job(
        "https://youtu.be/abc123DEF_-",
        force_reanalyze=True,
    )
    await service.process(await service.queue.get())

    assert len(first_transcriber.calls) == 1
    assert len(second_transcriber.calls) == 1


@pytest.mark.asyncio
async def test_analysis_job_stores_resolved_candidate_options(tmp_path: Path) -> None:
    service, _, _ = make_service(tmp_path)

    job = await service.create_analysis_job(
        "https://youtu.be/abc123DEF_-",
        candidate_count=3,
        min_duration_minutes=6,
        max_duration_minutes=9,
    )

    assert job.candidate_count == 3
    assert job.min_duration_minutes == 6
    assert job.max_duration_minutes == 9


@pytest.mark.asyncio
async def test_new_jobs_are_created_under_outputs_jobs(tmp_path: Path) -> None:
    service, _, _ = make_service(tmp_path)

    analysis = await service.create_analysis_job("https://youtu.be/abc123DEF_-")
    direct = await service.create_direct_render_job(
        "https://youtu.be/abc123DEF_-",
        start_seconds=10,
        end_seconds=20,
    )

    assert analysis.output_dir.parent == (tmp_path / "outputs" / "jobs").resolve()
    assert direct.output_dir.parent == (tmp_path / "outputs" / "jobs").resolve()


@pytest.mark.asyncio
async def test_render_skips_completed_candidate_and_force_creates_new_batch(
    tmp_path: Path,
) -> None:
    service, _, clip = make_service(tmp_path)
    job = await service.create_analysis_job("https://youtu.be/abc123DEF_-")
    await service.process(await service.queue.get())

    first = await service.create_render(
        job.job_id,
        CandidateSelectionRequest(candidate_ids=["A"]),
    )
    await service.process(await service.queue.get())
    skipped = await service.create_render(
        job.job_id,
        CandidateSelectionRequest(candidate_ids="A"),
    )
    forced = await service.create_render(
        job.job_id,
        CandidateSelectionRequest(candidate_ids="A", force_render=True),
    )
    await service.process(await service.queue.get())

    assert first.status == JobStatus.COMPLETED
    assert first.output_dir.name == "20260606-143006-id0002"
    assert skipped.status == JobStatus.COMPLETED
    assert forced.render_id != first.render_id
    assert clip.calls == ["A", "A"]


@pytest.mark.asyncio
async def test_candidate_render_is_nested_under_original_candidate_letter(
    tmp_path: Path,
) -> None:
    service, _, _ = make_service(tmp_path)
    job = await service.create_analysis_job("https://youtu.be/abc123DEF_-")
    await service.process(await service.queue.get())

    first = await service.create_render(
        job.job_id,
        CandidateSelectionRequest(candidate_ids="A", force_render=True),
    )
    await service.process(await service.queue.get())
    second = await service.create_render(
        job.job_id,
        CandidateSelectionRequest(candidate_ids="A", force_render=True),
    )
    await service.process(await service.queue.get())

    assert first.output_dir.parent.parent.name == "A"
    assert first.output_dir != second.output_dir
    assert {path.name for path in first.output_dir.iterdir()} == {
        "manifest.json",
        "video.mp4",
        "subtitles.zh-TW.srt",
        "subtitles.bilingual.ass",
        "youtube-metadata.json",
        "stage-manifest.json",
    }


@pytest.mark.asyncio
async def test_partial_render_failure_keeps_success_and_can_retry_failed_candidate(
    tmp_path: Path,
) -> None:
    service, _, clip = make_service(tmp_path)
    job = await service.create_analysis_job("https://youtu.be/abc123DEF_-")
    await service.process(await service.queue.get())
    clip.fail_candidates = {"B"}

    batch = await service.create_render(
        job.job_id,
        CandidateSelectionRequest(candidate_ids=["A", "B"]),
    )
    await service.process(await service.queue.get())

    assert batch.status == JobStatus.FAILED
    assert batch.candidate_results["A"].artifacts is not None
    assert batch.candidate_results["B"].error is not None
    clip.fail_candidates.clear()
    retry = await service.create_render(
        job.job_id,
        CandidateSelectionRequest(candidate_ids="B"),
    )
    await service.process(await service.queue.get())
    assert retry.status == JobStatus.COMPLETED
    assert retry.render_id == batch.render_id
    assert clip.calls == ["A", "B"]


@pytest.mark.asyncio
async def test_failed_candidate_render_resume_reports_pipeline_stage(
    tmp_path: Path,
) -> None:
    service, _, clip = make_service(tmp_path)
    job = await service.create_analysis_job("https://youtu.be/abc123DEF_-")
    await service.process(await service.queue.get())
    clip.fail_translate_candidates = {"A"}

    batch = await service.create_render(
        job.job_id,
        CandidateSelectionRequest(candidate_ids="A"),
    )
    await service.process(await service.queue.get())

    payload = json.loads((batch.output_dir / "stage-manifest.json").read_text())
    assert payload["stages"][-1]["stage"] == "translate_subtitles"
    assert payload["stages"][-1]["error"]["stage"] == "translate_subtitles"
    assert payload["stages"][-1]["error"]["details"]["inner_stage"] == "llm"
    assert payload["stages"][-1]["resume_strategy"] == (
        "rerun render to resume from translate_subtitles"
    )
    clip.fail_translate_candidates.clear()
    retry = await service.create_render(
        job.job_id,
        CandidateSelectionRequest(candidate_ids="A"),
    )
    await service.process(await service.queue.get())

    retry_payload = json.loads((retry.output_dir / "stage-manifest.json").read_text())
    assert retry.render_id == batch.render_id
    retry_manifest = StageManifest.model_validate_json(
        (retry.output_dir / "stage-manifest.json").read_text()
    )
    assert retry_manifest.resume_from is None
    skipped_cut = [
        stage
        for stage in retry_payload["stages"]
        if stage["stage"] == "cut_clip" and stage["status"] == "skipped"
    ][-1]
    assert skipped_cut["reused"] is True
    assert retry.status == JobStatus.COMPLETED
    assert clip.calls == ["A"]


@pytest.mark.asyncio
async def test_failed_candidate_render_writes_failed_stage_manifest(tmp_path: Path) -> None:
    service, _, clip = make_service(tmp_path)
    job = await service.create_analysis_job("https://youtu.be/abc123DEF_-")
    await service.process(await service.queue.get())
    clip.fail_candidates = {"A"}

    batch = await service.create_render(
        job.job_id,
        CandidateSelectionRequest(candidate_ids="A"),
    )
    await service.process(await service.queue.get())

    stage_manifest_path = batch.output_dir / "stage-manifest.json"
    payload = json.loads(stage_manifest_path.read_text(encoding="utf-8"))
    assert payload["stages"][-1]["stage"] == "burn_subtitles"
    assert payload["stages"][-1]["status"] == "failed"
    assert payload["stages"][-1]["error"]["error_code"] == "VIDEO_RENDER_FAILED"
    assert payload["stages"][-1]["resume_strategy"] == (
        "rerun render to resume from burn_subtitles"
    )


@pytest.mark.asyncio
async def test_render_reports_removed_source_artifact(tmp_path: Path) -> None:
    service, _, clip = make_service(tmp_path)
    job = await service.create_analysis_job("https://youtu.be/abc123DEF_-")
    await service.process(await service.queue.get())
    assert job.source_artifacts is not None
    job.source_artifacts.source_video.unlink()

    batch = await service.create_render(
        job.job_id,
        CandidateSelectionRequest(candidate_ids="A"),
    )
    await service.process(await service.queue.get())

    assert batch.status == JobStatus.FAILED
    assert batch.candidate_results["A"].error is not None
    assert (
        batch.candidate_results["A"].error.error_code
        == ErrorCode.SOURCE_CACHE_MISSING
    )
    payload = json.loads(
        (batch.output_dir / "stage-manifest.json").read_text(encoding="utf-8")
    )
    assert payload["stages"][-1]["stage"] == "source_ingestion"
    assert payload["stages"][-1]["error"]["stage"] == "source_ingestion"
    assert clip.calls == []


@pytest.mark.asyncio
async def test_candidate_render_writes_stage_manifest(tmp_path: Path) -> None:
    service, _, _ = make_service(tmp_path)
    job = await service.create_analysis_job("https://youtu.be/abc123DEF_-")
    await service.process(await service.queue.get())

    batch = await service.create_render(
        job.job_id,
        CandidateSelectionRequest(candidate_ids="A", force_render=True),
    )
    await service.process(await service.queue.get())

    stage_manifest_path = batch.output_dir / "stage-manifest.json"
    assert stage_manifest_path.is_file()
    payload = json.loads(stage_manifest_path.read_text(encoding="utf-8"))
    assert [stage["stage"] for stage in payload["stages"]] == [
        "cut_clip",
        "translate_subtitles",
        "write_subtitles",
        "burn_subtitles",
        "generate_metadata",
        "validate_render",
    ]
    assert all(stage["status"] == "completed" for stage in payload["stages"])
    assert payload["stages"][0]["artifacts"]["temporary_clip"].endswith(
        "A.unburned.mp4"
    )


@pytest.mark.asyncio
async def test_candidate_render_writes_running_stage_manifest(tmp_path: Path) -> None:
    service, _, _ = make_service(tmp_path)
    blocking_clip = BlockingCutClip()
    service.clip_engine = blocking_clip
    job = await service.create_analysis_job("https://youtu.be/abc123DEF_-")
    await service.process(await service.queue.get())

    batch = await service.create_render(
        job.job_id,
        CandidateSelectionRequest(candidate_ids="A", force_render=True),
    )
    work = await service.queue.get()
    task = asyncio.create_task(service.process(work))
    try:
        await asyncio.wait_for(blocking_clip.started.wait(), timeout=1)
        stage_manifest_path = batch.output_dir / "stage-manifest.json"
        assert stage_manifest_path.is_file()
        payload = json.loads(stage_manifest_path.read_text(encoding="utf-8"))
        assert payload["stages"][-1]["stage"] == "cut_clip"
        assert payload["stages"][-1]["status"] == "running"
        assert payload["stages"][-1]["started_at"] is not None
        assert blocking_clip.calls == []
    finally:
        blocking_clip.release.set()
        await task


@pytest.mark.asyncio
async def test_invalid_existing_stage_manifest_does_not_escape_render_failure(
    tmp_path: Path,
) -> None:
    service, _, _ = make_service(tmp_path)
    job = await service.create_analysis_job("https://youtu.be/abc123DEF_-")
    await service.process(await service.queue.get())

    batch = await service.create_render(
        job.job_id,
        CandidateSelectionRequest(candidate_ids="A", force_render=True),
    )
    batch.output_dir.mkdir(parents=True, exist_ok=True)
    (batch.output_dir / "stage-manifest.json").write_text("{", encoding="utf-8")

    await service.process(await service.queue.get())

    assert batch.status == JobStatus.FAILED
    assert batch.candidate_results["A"].error is not None
    manifest = RenderManifest.model_validate_json(
        (batch.output_dir / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest.render_state is RenderState.FAILED


@pytest.mark.asyncio
async def test_render_rejects_unknown_candidate(tmp_path: Path) -> None:
    service, _, _ = make_service(tmp_path)
    job = await service.create_analysis_job("https://youtu.be/abc123DEF_-")
    await service.process(await service.queue.get())

    with pytest.raises(InsightCastError) as exc_info:
        await service.create_render(
            job.job_id,
            CandidateSelectionRequest(candidate_ids="Z"),
        )

    assert exc_info.value.error_code == ErrorCode.CANDIDATE_NOT_FOUND


@pytest.mark.asyncio
async def test_render_rejects_analysis_job_before_selection_is_ready(
    tmp_path: Path,
) -> None:
    service, _, _ = make_service(tmp_path)
    job = await service.create_analysis_job("https://youtu.be/abc123DEF_-")

    with pytest.raises(InsightCastError) as exc_info:
        await service.create_render(
            job.job_id,
            CandidateSelectionRequest(candidate_ids="A"),
        )

    assert exc_info.value.error_code.value == "INVALID_JOB_STATE"
    assert exc_info.value.details == {
        "job_id": job.job_id,
        "status": JobStatus.QUEUED,
    }


@pytest.mark.asyncio
async def test_direct_render_is_unique_and_does_not_call_curator(tmp_path: Path) -> None:
    service, curator, _ = make_service(tmp_path)

    first = await service.create_direct_render_job(
        "https://youtu.be/abc123DEF_-",
        start_seconds=10,
        end_seconds=20,
    )
    second = await service.create_direct_render_job(
        "https://youtu.be/abc123DEF_-",
        start_seconds=10,
        end_seconds=20,
    )
    await service.process(await service.queue.get())
    await service.process(await service.queue.get())

    assert first.job_id != second.job_id
    assert first.status == JobStatus.COMPLETED
    assert second.status == JobStatus.COMPLETED
    assert curator.discovery_calls == 0
    assert curator.selection_calls == 0
    assert isinstance(first.artifacts, RenderArtifacts)
    assert all(item.kind == WorkKind.DIRECT_RENDER for item in service.processed_work[-2:])


@pytest.mark.asyncio
async def test_direct_render_uses_video_level_custom_directory(tmp_path: Path) -> None:
    service, _, _ = make_service(tmp_path)
    job = await service.create_direct_render_job(
        "https://youtu.be/abc123DEF_-",
        start_seconds=10,
        end_seconds=20,
    )

    await service.process(await service.queue.get())

    assert job.output_dir.parent.name == "custom"
    assert {path.name for path in job.output_dir.iterdir()} == {
        "manifest.json",
        "video.mp4",
        "subtitles.zh-TW.srt",
        "subtitles.bilingual.ass",
        "youtube-metadata.json",
        "stage-manifest.json",
    }

    payload = json.loads((job.output_dir / "stage-manifest.json").read_text(encoding="utf-8"))
    assert [stage["stage"] for stage in payload["stages"]] == [
        "cut_clip",
        "translate_subtitles",
        "write_subtitles",
        "burn_subtitles",
        "generate_metadata",
        "validate_render",
    ]
    assert all(stage["status"] == "completed" for stage in payload["stages"])


@pytest.mark.asyncio
async def test_analysis_emits_concise_task_progress_events(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    service, _curator, _clip = make_service(tmp_path)

    with caplog.at_level(logging.INFO, logger="insightcast.task"):
        job = await service.create_analysis_job("https://youtu.be/abc123DEF_-")
        await service.process(await service.queue.get())

    messages = [record.getMessage() for record in caplog.records]
    assert (
        f"task job_id={job.job_id} type=ANALYSIS status=QUEUED "
        "message='Analysis job is queued.'"
    ) in messages
    assert (
        f"task job_id={job.job_id} type=ANALYSIS status=TRANSCRIBING "
        "message='Transcribing English audio.'"
    ) in messages
    assert (
        f"task job_id={job.job_id} type=ANALYSIS "
        "stage=topic_discovery event=started"
    ) in messages
    assert any(
        message.startswith(
            f"task job_id={job.job_id} type=ANALYSIS "
            "stage=topic_discovery event=completed elapsed_seconds="
        )
        for message in messages
    )
    assert (
        f"task job_id={job.job_id} type=ANALYSIS status=WAITING_SELECTION "
        "message='2 candidates are ready for selection.'"
    ) in messages
    assert any(
        "InsightCast task_summary" in message
        and f"job_id={job.job_id}" in message
        and "type=ANALYSIS" in message
        and "event: 'analysis_completed'" in message
        and "stage_topic_discovery_seconds:" in message
        and "llm_total_tokens: 8" in message
        for message in messages
    )


@pytest.mark.asyncio
async def test_failed_direct_render_retains_failed_manifest(tmp_path: Path) -> None:
    service, _, clip = make_service(tmp_path)
    clip.fail_candidates = {"custom"}
    job = await service.create_direct_render_job(
        "https://youtu.be/abc123DEF_-",
        start_seconds=10,
        end_seconds=20,
    )

    await service.process(await service.queue.get())

    manifest = RenderManifest.model_validate_json(
        (job.output_dir / "manifest.json").read_text(encoding="utf-8")
    )
    assert job.status == JobStatus.FAILED
    assert manifest.render_state is RenderState.FAILED
    assert manifest.render_error is not None
    assert job.output_dir.is_dir()


@pytest.mark.asyncio
async def test_analysis_failure_writes_traceback_to_pipeline_log(tmp_path: Path) -> None:
    class FailingSource:
        async def ingest(self, **_kwargs: object) -> SourceResult:
            raise RuntimeError("unexpected source failure")

    service = JobService(
        output_root=tmp_path / "outputs",
        work_root=tmp_path / ".work",
        source_engine=FailingSource(),
        transcription_client=FakeTranscriber(),
        curator_engine=FakeCurator(),
        clip_engine=FakeClip(),
        publish_engine=FakePublish(),
        writer=FileJobWriter(),
        clock=Clock(),
        id_factory=IdFactory(),
    )
    job = await service.create_analysis_job("https://youtu.be/abc123DEF_-")

    await service.process(await service.queue.get())

    log = get_job_log_path(job.job_id, job.output_dir).read_text(encoding="utf-8")
    assert "unexpected source failure" in log
    assert "Traceback" in log


@pytest.mark.asyncio
async def test_failed_analysis_emits_structured_task_failure(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    service = JobService(
        output_root=tmp_path / "outputs",
        work_root=tmp_path / ".work",
        source_engine=FakeSource(),
        transcription_client=FakeTranscriber(),
        curator_engine=FailingCurator(),
        clip_engine=FakeClip(),
        publish_engine=FakePublish(),
        writer=FileJobWriter(),
        clock=Clock(),
        id_factory=IdFactory(),
    )

    with caplog.at_level(logging.ERROR, logger="insightcast.task"):
        job = await service.create_analysis_job("https://youtu.be/abc123DEF_-")
        await service.process(await service.queue.get())

    messages = [record.getMessage() for record in caplog.records]
    assert (
        f"task job_id={job.job_id} type=ANALYSIS event=failed "
        "error_code=INSUFFICIENT_CANDIDATES stage=topic_discovery"
    ) in messages
    assert all(record.exc_info is None for record in caplog.records)


@pytest.mark.asyncio
async def test_failed_candidate_render_emits_structured_task_failure(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    service, _curator, clip = make_service(tmp_path)
    job = await service.create_analysis_job("https://youtu.be/abc123DEF_-")
    await service.process(await service.queue.get())
    clip.fail_candidates.add("A")
    await service.create_render(
        job.job_id,
        CandidateSelectionRequest(candidate_ids="A"),
    )

    with caplog.at_level(logging.ERROR, logger="insightcast.task"):
        await service.process(await service.queue.get())

    messages = [record.getMessage() for record in caplog.records]
    assert (
        f"task job_id={job.job_id} type=ANALYSIS event=failed "
        "error_code=VIDEO_RENDER_FAILED stage=burn_subtitles"
    ) in messages


@pytest.mark.asyncio
async def test_analysis_removes_provisional_output_after_final_directory_is_known(
    tmp_path: Path,
) -> None:
    service = JobService(
        output_root=tmp_path / "outputs",
        work_root=tmp_path / ".work",
        source_engine=FakeSource(),
        transcription_client=FakeTranscriber(),
        curator_engine=FakeCurator(),
        clip_engine=FakeClip(),
        publish_engine=FakePublish(),
        writer=FileJobWriter(),
        clock=Clock(),
        id_factory=IdFactory(),
    )
    job = await service.create_analysis_job("https://youtu.be/abc123DEF_-")
    provisional_dir = job.output_dir

    await service.process(await service.queue.get())

    assert job.output_dir != provisional_dir
    assert job.output_dir == (
        tmp_path
        / "outputs"
        / "videos"
        / "abc123DEF_-_source"
        / "analyses"
        / "20260606-143000-id0001"
    ).resolve()
    assert not provisional_dir.exists()
    log = get_job_log_path(job.job_id, job.output_dir).read_text(encoding="utf-8")
    assert "WAITING_SELECTION" in log


@pytest.mark.asyncio
async def test_pipeline_log_records_analysis_and_render_stage_timings(
    tmp_path: Path,
) -> None:
    service = JobService(
        output_root=tmp_path / "outputs",
        work_root=tmp_path / ".work",
        source_engine=FakeSource(),
        transcription_client=ProgressFakeTranscriber(),
        curator_engine=FakeCurator(),
        clip_engine=FakeClip(),
        publish_engine=FakePublish(),
        writer=FileJobWriter(),
        clock=Clock(),
        id_factory=IdFactory(),
    )
    job = await service.create_analysis_job("https://youtu.be/abc123DEF_-")
    await service.process(await service.queue.get())
    await service.create_render(
        job.job_id,
        CandidateSelectionRequest(candidate_ids="A"),
    )
    await service.process(await service.queue.get())

    log = get_job_log_path(job.job_id, job.output_dir).read_text(encoding="utf-8")
    assert "source_cache_miss" in log
    assert "transcription_progress video_id='abc123DEF_-' event='planned'" in log
    assert "transcription_progress video_id='abc123DEF_-' event='completed'" in log
    assert (
        "llm_telemetry event='completed' trace_name='topic_discovery' "
        "model='gpt-curator'"
    ) in log
    assert "input_tokens=5" in log
    assert "total_tokens=8" in log
    assert "InsightCast task_summary" in log
    assert "event: 'analysis_completed'" in log
    assert "llm_topic_discovery_total_tokens: 8" in log
    assert "processed_chunks=2" in log
    assert "chunk_count=2" in log
    for stage in (
        "source_ingestion",
        "transcription",
        "topic_discovery",
        "candidate_boundary_selection",
        "cut_clip",
        "translate_subtitles",
        "write_subtitles",
        "burn_subtitles",
        "generate_metadata",
        "validate_render",
    ):
        assert f"stage_started stage={stage}" in log
        assert f"stage_completed stage={stage}" in log
    assert "stage_started stage=candidate_curation" not in log
    assert "elapsed_seconds=" in log
    assert "event: 'render_completed'" in log
    assert "stage_burn_subtitles_seconds:" in log


@pytest.mark.asyncio
async def test_transcription_progress_is_emitted_to_task_log(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    service = JobService(
        output_root=tmp_path / "outputs",
        work_root=tmp_path / ".work",
        source_engine=FakeSource(),
        transcription_client=ProgressFakeTranscriber(),
        curator_engine=FakeCurator(),
        clip_engine=FakeClip(),
        publish_engine=FakePublish(),
        writer=FileJobWriter(),
        clock=Clock(),
        id_factory=IdFactory(),
    )
    job = await service.create_analysis_job("https://youtu.be/abc123DEF_-")

    with caplog.at_level(logging.INFO, logger="insightcast.task"):
        await service.process(await service.queue.get())

    messages = [record.getMessage() for record in caplog.records]
    assert any(
        f"transcription_progress job_id={job.job_id} type=ANALYSIS "
        "stage=transcription video_id='abc123DEF_-' event='planned'" in message
        for message in messages
    )
    assert any(
        "event='completed_all'" in message and "processed_chunks=2" in message
        for message in messages
    )
    assert job.progress is not None
    assert job.progress["stage"] == "transcription"
    assert job.progress["event"] == "completed_all"
    assert job.progress["chunk_count"] == 2
    assert job.progress["processed_chunks"] == 2
