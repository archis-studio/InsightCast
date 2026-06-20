import json

PROMPT_VERSION = "metadata-v3"
SYSTEM_PROMPT = """Create evidence-grounded Traditional Chinese YouTube metadata for a
translated highlight from a foreign-language source video. The Traditional Chinese title
should lead with the selected segment's viewer-facing value, while preserving one
recognizable source title element when it improves trust, searchability, or attribution.
Do not force the full original title into every title. Blend the source title element with
the highlight's specific focus so viewers understand both where the segment came from and
why this excerpt is worth watching.

Prefer titles that feel natural on Traditional Chinese YouTube: clear, specific,
curiosity-driven, and grounded. A question, colon, corner brackets, or vertical bar may be
used when it improves readability. Strong framing is allowed only when supported by the
summary or transcript. Avoid clickbait and unsupported urgency, certainty, conflict,
consequences, or claims that everyone is shocked or that the topic changes everything.

The description must explain significance, summarize reasoning with necessary context,
disclose that this is a Traditional Chinese translated highlight, and direct viewers to
the original video for the complete discussion. Return title, description, accurate tags,
and a privacy status that defaults to private."""


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
                "traditional_chinese_viewer_value_first",
                "selected_highlight_focus",
                "source_title_element_for_trust_search_or_attribution",
                "grounded_curiosity_without_clickbait",
            ],
            "source_title_retention_strategy": [
                "preserve_one_recognizable_source_title_element",
                "do_not_force_the_full_original_title",
                "blend_source_title_element_with_selected_highlight_focus",
            ],
            "highlight_positioning": (
                "Package the selected segment as a valuable Traditional Chinese highlight, "
                "not as a replacement for the full original video."
            ),
            "title_style_examples": [
                "如何讓演講更有說服力？《How to Speak》精華：Vision、Contribution 與強收尾",
                "AI 如何改變工作？《原標題關鍵詞》精華：最值得重看的 12 分鐘",
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
