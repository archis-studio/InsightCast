from pathlib import Path

from fastapi import APIRouter, status

from insightcast.api.dependencies import VideoStoreDependency
from insightcast.api.schemas import (
    ErrorResponse,
    VideoAnalysisItem,
    VideoAnalysisListResponse,
    VideoRenderItem,
    VideoRenderListResponse,
    VideoResponse,
)
from insightcast.core.exceptions import InsightCastError
from insightcast.domain.enums import ErrorCode
from insightcast.domain.models import RenderArtifacts
from insightcast.storage.video_store import AnalysisEntry, RenderEntry

router = APIRouter(prefix="/api/v1/videos", tags=["videos"])
ERROR_RESPONSES = {
    400: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    500: {"model": ErrorResponse},
}


def _artifact_paths(artifacts: RenderArtifacts | None) -> dict[str, Path]:
    if artifacts is None:
        return {}
    return artifacts.model_dump()


def _analysis_item(entry: AnalysisEntry) -> VideoAnalysisItem:
    manifest = entry.manifest
    return VideoAnalysisItem(
        analysis_id=manifest.analysis_id,
        operation_id=manifest.operation_id,
        state=manifest.state,
        created_at=manifest.created_at,
        completed_at=manifest.completed_at,
        transcript_id=manifest.transcript_id,
        candidate_count=manifest.candidate_count,
        candidates_path=entry.candidates_path,
        candidate_paths=entry.candidate_paths,
        manifest_path=entry.manifest_path,
    )


def _render_item(entry: RenderEntry) -> VideoRenderItem:
    manifest = entry.manifest
    return VideoRenderItem(
        render_id=manifest.render_id,
        operation_id=manifest.operation_id,
        kind=manifest.kind,
        analysis_id=manifest.analysis_id,
        candidate_id=manifest.candidate_id,
        start_seconds=manifest.start_seconds,
        end_seconds=manifest.end_seconds,
        render_state=manifest.render_state,
        publish_state=manifest.publish_state,
        created_at=manifest.created_at,
        completed_at=manifest.completed_at,
        manifest_path=entry.manifest_path,
        artifacts=_artifact_paths(entry.artifacts),
    )


@router.get(
    "/{video_id}",
    response_model=VideoResponse,
    summary="Get persisted video state",
    responses=ERROR_RESPONSES,
)
async def get_video(
    video_id: str,
    video_store: VideoStoreDependency,
) -> VideoResponse:
    video = video_store.find_video(video_id)
    if video is None:
        raise InsightCastError(
            ErrorCode.JOB_NOT_FOUND,
            "The requested video does not exist.",
            details={"video_id": video_id},
        )
    return VideoResponse(
        video_id=video.manifest.video_id,
        title=video.manifest.title,
        uploader=video.manifest.uploader,
        upload_date=video.manifest.upload_date,
        original_youtube_url=video.manifest.original_youtube_url,
        normalized_youtube_url=video.manifest.normalized_youtube_url,
        first_seen_at=video.manifest.first_seen_at,
        last_seen_at=video.manifest.last_seen_at,
        root=video.root,
        manifest_path=video.root / "video.json",
    )


@router.get(
    "/{video_id}/analyses",
    response_model=VideoAnalysisListResponse,
    summary="List persisted analyses for a video",
    responses=ERROR_RESPONSES,
)
async def list_video_analyses(
    video_id: str,
    video_store: VideoStoreDependency,
) -> VideoAnalysisListResponse:
    return VideoAnalysisListResponse(
        video_id=video_id,
        analyses=[_analysis_item(entry) for entry in video_store.list_analyses(video_id)],
    )


@router.get(
    "/{video_id}/renders",
    response_model=VideoRenderListResponse,
    summary="List publishable renders for a video",
    responses=ERROR_RESPONSES,
)
async def list_video_renders(
    video_id: str,
    video_store: VideoStoreDependency,
) -> VideoRenderListResponse:
    return VideoRenderListResponse(
        video_id=video_id,
        renders=[
            _render_item(entry)
            for entry in video_store.list_publishable_renders(video_id)
        ],
    )


@router.post(
    "/{video_id}/renders/{render_id}/youtube-uploads",
    response_model=ErrorResponse,
    status_code=status.HTTP_501_NOT_IMPLEMENTED,
    summary="Validate an explicit render for a future YouTube upload",
    responses=ERROR_RESPONSES,
)
async def upload_render_stub(
    video_id: str,
    render_id: str,
    video_store: VideoStoreDependency,
) -> ErrorResponse:
    render = video_store.resolve_publishable_render(video_id, render_id)
    assert render.artifacts is not None
    raise InsightCastError(
        ErrorCode.UPLOAD_NOT_IMPLEMENTED,
        "YouTube uploading is not implemented in the MVP.",
        details={
            "video_id": video_id,
            "render_id": render.manifest.render_id,
            "burned_video": str(render.artifacts.burned_video),
            "youtube_metadata": str(render.artifacts.youtube_metadata),
        },
    )
