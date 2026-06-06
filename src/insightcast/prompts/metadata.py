import json

PROMPT_VERSION = "metadata-v1"
SYSTEM_PROMPT = """Create thoughtful Traditional Chinese YouTube metadata for knowledge content.
Avoid clickbait and preserve the source's meaning. Return title, description, tags, and a privacy
status that defaults to private."""


def build_user_prompt(
    *,
    source_title: str,
    summary: str,
    transcript_excerpt: str,
) -> str:
    return json.dumps(
        {
            "source_title": source_title,
            "summary": summary,
            "transcript_excerpt": transcript_excerpt,
        },
        ensure_ascii=False,
        indent=2,
    )

