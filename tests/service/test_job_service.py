from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from insightcast.core.exceptions import InsightCastError
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
from insightcast.engines.curator_engine import CurationResult
from insightcast.engines.publish_engine import GeneratedYouTubeMetadata
from insightcast.engines.source_engine import SourceResult
from insightcast.infrastructure.ytdlp_client import YouTubeMetadata
from insightcast.services.job_service import JobService, WorkKind
from insightcast.storage.file_job_writer import FileJobWriter


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
        output_dir = Path(kwargs["output_root"]) / f"final-{kwargs['job_id']}"
        source_dir = output_dir / "source"
        return SourceResult(
            output_dir=output_dir,
            metadata=YouTubeMetadata(
                video_id="abc123DEF_-",
                title="Source",
                duration_seconds=1200,
                webpage_url=str(kwargs["youtube_url"]),
            ),
            source_artifacts=SourceArtifacts(
                source_video=source_dir / "source.mp4",
                source_audio=source_dir / "audio.mp3",
            ),
        )


class FakeTranscriber:
    async def transcribe(self, _path: Path) -> Transcript:
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


class FakeCurator:
    def __init__(self) -> None:
        self.calls = 0

    async def curate(self, **_kwargs: object) -> CurationResult:
        self.calls += 1
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
            prompt_version="curator-v1",
        )


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
        base_name = str(kwargs["base_name"])
        return ClipArtifacts(
            traditional_chinese_srt=output_dir / f"{base_name}.zh-TW.srt",
            bilingual_ass=output_dir / f"{base_name}.bilingual.ass",
            burned_video=output_dir / f"{base_name}.bilingual.burned.mp4",
        )


class FakePublish:
    async def generate(self, **_kwargs: object) -> GeneratedYouTubeMetadata:
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
    assert curator.calls == 1


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
    assert skipped.status == JobStatus.COMPLETED
    assert forced.render_id != first.render_id
    assert clip.calls == ["A", "A"]


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
    assert curator.calls == 0
    assert isinstance(first.artifacts, RenderArtifacts)
    assert all(item.kind == WorkKind.DIRECT_RENDER for item in service.processed_work[-2:])


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

    log = (job.output_dir / "pipeline.log").read_text(encoding="utf-8")
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
    assert not provisional_dir.exists()
    log = (job.output_dir / "pipeline.log").read_text(encoding="utf-8")
    assert "WAITING_SELECTION" in log
