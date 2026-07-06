import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from insightcast.infrastructure.ytdlp_client import YouTubeMetadata
from insightcast.prompts import metadata

INSIGHTCAST_DESCRIPTION_DISCLAIMER = (
    "InsightCast 為繁體中文翻譯精選，非完整原片；完整脈絡請參考原始影片。"
)


class PublishModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TitleVariant(PublishModel):
    title: str = Field(min_length=1)
    strategy: Literal[
        "source_equity_hook",
        "mechanism_breakdown",
        "audience_pain_reframe",
    ]
    rationale: str = Field(min_length=1)


class GeneratedYouTubeMetadata(PublishModel):
    title: str = Field(min_length=1)
    title_variants: list[TitleVariant] = Field(min_length=3, max_length=3)
    description: str = Field(min_length=1)
    tags: list[str]
    privacy_status: Literal["private", "unlisted", "public"] = "private"

    @model_validator(mode="after")
    def validate_title_packaging(self) -> "GeneratedYouTubeMetadata":
        titles = [self.title, *(variant.title for variant in self.title_variants)]
        for title in titles:
            _validate_title_shape(title)
        variant_titles = {variant.title for variant in self.title_variants}
        if self.title not in variant_titles:
            raise ValueError("primary title must match one title variant")
        strategies = {variant.strategy for variant in self.title_variants}
        expected = {
            "source_equity_hook",
            "mechanism_breakdown",
            "audience_pain_reframe",
        }
        if strategies != expected:
            raise ValueError("title variants must include each required strategy exactly once")
        return self


class PublishEngine:
    def __init__(self, *, client: Any, model: str, writer: Any) -> None:
        self.client = client
        self.model = model
        self.writer = writer

    async def generate(
        self,
        *,
        source_metadata: YouTubeMetadata,
        candidate_suggested_title: str | None = None,
        summary: str,
        transcript_excerpt: str,
        candidate_core_claim: str | None = None,
        candidate_payoff: str | None = None,
        candidate_argument_arc: Sequence[str] | None = None,
        candidate_boundary_notes: Mapping[str, Any] | None = None,
        destination: Path,
    ) -> GeneratedYouTubeMetadata:
        generated = await self.client.parse(
            model=self.model,
            system_prompt=metadata.SYSTEM_PROMPT,
            user_prompt=metadata.build_user_prompt(
                source_title=source_metadata.title,
                source_description=source_metadata.description,
                candidate_suggested_title=candidate_suggested_title,
                summary=summary,
                transcript_excerpt=transcript_excerpt,
                candidate_core_claim=candidate_core_claim,
                candidate_payoff=candidate_payoff,
                candidate_argument_arc=candidate_argument_arc,
                candidate_boundary_notes=candidate_boundary_notes,
            ),
            response_model=GeneratedYouTubeMetadata,
            trace_name="generate_metadata",
        )
        generated = generated.model_copy(
            update={
                "description": _normalize_description(generated.description),
            }
        )
        self.writer.write_json(
            destination,
            {
                "source": source_metadata.model_dump(mode="json"),
                "generated": generated.model_dump(mode="json"),
                "trace": {
                    "model": self.model,
                    "prompt_version": metadata.PROMPT_VERSION,
                },
            },
        )
        return generated


def _normalize_description(description: str) -> str:
    without_brand_mentions = re.sub(
        r"[^。！？.!?]*InsightCast[^。！？.!?]*[。！？.!?]?",
        "",
        description,
    )
    compacted = " ".join(without_brand_mentions.split()).strip()
    if not compacted:
        return INSIGHTCAST_DESCRIPTION_DISCLAIMER
    return f"{compacted} {INSIGHTCAST_DESCRIPTION_DISCLAIMER}"


def _validate_title_shape(title: str) -> None:
    if "｜" in title or "|" in title:
        raise ValueError("generated title must not contain a vertical bar")
    if title.count("：") != 1:
        raise ValueError("generated title must contain exactly one fullwidth colon")
    if len(title) > 100:
        raise ValueError("generated title must be 100 characters or fewer")
