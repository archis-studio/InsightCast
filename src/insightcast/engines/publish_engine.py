from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from insightcast.infrastructure.ytdlp_client import YouTubeMetadata
from insightcast.prompts import metadata


class PublishModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GeneratedYouTubeMetadata(PublishModel):
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    tags: list[str]
    privacy_status: Literal["private", "unlisted", "public"] = "private"


class PublishEngine:
    def __init__(self, *, client: Any, model: str, writer: Any) -> None:
        self.client = client
        self.model = model
        self.writer = writer

    async def generate(
        self,
        *,
        source_metadata: YouTubeMetadata,
        summary: str,
        transcript_excerpt: str,
        destination: Path,
    ) -> GeneratedYouTubeMetadata:
        generated = await self.client.parse(
            model=self.model,
            system_prompt=metadata.SYSTEM_PROMPT,
            user_prompt=metadata.build_user_prompt(
                source_title=source_metadata.title,
                summary=summary,
                transcript_excerpt=transcript_excerpt,
            ),
            response_model=GeneratedYouTubeMetadata,
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

