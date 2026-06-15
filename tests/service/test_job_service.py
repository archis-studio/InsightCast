import asyncio
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
from insightcast.engines.clip_engine import ClipArtifacts
from insightcast.engines.curator_engine import (
    CurationResult,
    TopicDiscoveryOutput,
    TopicDiscoveryResponse,
)
from insightcast.engines.publish_engine import GeneratedYouTubeMetadata
from insightcast.engines.source_engine import SourceResult
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
            prompt_version="topic-discovery-v1+curator-v3",
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
            description="Description",
            tags=["tag"],
        )


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
    assert clip.calls == []


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
    }


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
    await service.create_render(
        job.job_id,
        CandidateSelectionRequest(candidate_ids="A"),
    )
    await service.process(await service.queue.get())

    log = get_job_log_path(job.job_id, job.output_dir).read_text(encoding="utf-8")
    assert "source_cache_miss" in log
    for stage in (
        "source_ingestion",
        "transcription",
        "topic_discovery",
        "candidate_boundary_selection",
        "candidate_clip_render",
        "metadata_generation",
    ):
        assert f"stage_started stage={stage}" in log
        assert f"stage_completed stage={stage}" in log
    assert "stage_started stage=candidate_curation" not in log
    assert "elapsed_seconds=" in log
