import json

PROMPT_VERSION = "metadata-v2"
SYSTEM_PROMPT = """Create evidence-grounded Traditional Chinese knowledge-news metadata
for a translated highlight from a foreign-language YouTube video. The title should state
what happened, was found, or is being argued, the central conclusion or consequence, and
why it deserves attention. Strong framing is allowed only when supported by the summary or
transcript. Avoid clickbait and unsupported urgency, certainty, conflict, consequences, or
claims that everyone is shocked or that the topic changes everything. The description must
explain significance, summarize reasoning with necessary context, disclose that this is a
Traditional Chinese translated highlight, and direct viewers to the original video for the
complete discussion. Return title, description, accurate tags, and a privacy status that
defaults to private."""


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
            "title_strategy": [
                "what_happened_was_found_or_is_argued",
                "central_conclusion_or_consequence",
                "why_it_deserves_attention",
            ],
            "description_strategy": [
                "why_the_topic_matters",
                "central_claim_and_supporting_reasoning",
                "necessary_context",
                "traditional_chinese_translated_highlight_disclosure",
                "consult_original_video_for_full_discussion",
            ],
            "tag_strategy": (
                "Use only people, organizations, subjects, and concepts supported "
                "by the selected segment."
            ),
        },
        ensure_ascii=False,
        indent=2,
    )
