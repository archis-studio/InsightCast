import json

PROMPT_VERSION = "metadata-v4"
SYSTEM_PROMPT = """Create evidence-grounded Traditional Chinese YouTube metadata for an
InsightCast translated knowledge highlight from a foreign-language source video.

You are the packaging editor for InsightCast. The brand voice is editorial, precise,
premium but plainspoken, and curious without hype. Package the clip so Traditional Chinese
viewers can quickly decide why this specific idea is worth their attention. The
Traditional Chinese title should lead with a concrete viewer outcome, useful tension, or
central insight from the selected segment. It should name the selected idea rather than
pretending to summarize the whole episode.

Preserve one recognizable source title, guest, creator, show, or concept element only when
it improves trust, searchability, or attribution. Do not force the full original title into
every title. The source anchor must support the clip value, not overpower it.

Prefer titles that feel natural on Traditional Chinese YouTube: short enough for mobile
scanning, clear, specific, curiosity-driven, and grounded. A question, colon, corner
brackets, or vertical bar may be used when it improves readability. Avoid generic prefixes
such as 影片主張, 這段精華, or 作者主張. Strong framing is allowed only when supported by
the summary or transcript. Avoid clickbait and unsupported urgency, certainty, conflict,
guarantees, consequences, or claims that everyone is shocked or that the topic changes
everything.

The description should read like publishable channel copy, not a raw summary. Open with a
hook for the target viewer, explain why the clip matters now, then summarize what the
viewer will understand after watching with enough reasoning or examples to feel concrete.
Disclose that this is a Traditional Chinese translated highlight and direct viewers to the
original video for the full context. Return title, description, accurate tags, and a
privacy status that defaults to private."""


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
            "brand_positioning": {
                "product": "InsightCast",
                "promise": (
                    "help Traditional Chinese viewers quickly decide why this foreign-language "
                    "knowledge highlight is worth their attention"
                ),
                "voice": [
                    "editorial",
                    "precise",
                    "premium_but_plainspoken",
                    "curious_without_hype",
                ],
            },
            "title_strategy": [
                "lead_with_viewer_outcome_or_core_tension",
                "name_the_specific_idea_not_the_whole_episode",
                "use_one_source_anchor_for_trust_when_helpful",
                "make_it_clickable_without_sounding_like_clickbait",
            ],
            "title_quality_bar": [
                "specific_enough_to_stand_without_the_original_title",
                "short_enough_for_mobile_scanning",
                "no_generic_prefix_like_影片主張_or_這段精華",
                "no_unsupported_superlatives_or_guarantees",
            ],
            "source_title_retention_strategy": [
                "preserve_one_recognizable_source_title_element_when_it_adds_trust",
                "do_not_force_the_full_original_title",
                "do_not_let_source_title_overpower_the_clip_value",
                "blend_source_anchor_with_selected_highlight_focus",
            ],
            "highlight_positioning": (
                "Package the selected segment as a standalone InsightCast knowledge highlight "
                "for Traditional Chinese viewers, not as a replacement for the full original video."
            ),
            "title_style_examples": [
                "讓簡報被記住的關鍵：Vision、Contribution 與強收尾｜How to Speak",
                "別再只改 Prompt：Karpathy 的三層 AI 工作法",
            ],
            "description_strategy": [
                "opening_hook_for_target_viewer",
                "why_this_clip_matters_now",
                "what_the_viewer_will_understand_after_watching",
                "key_reasoning_or_examples_from_the_segment",
                "traditional_chinese_translated_highlight_disclosure",
                "original_video_attribution_for_full_context",
            ],
            "description_structure": [
                "one_sentence_hook",
                "2_to_3_short_paragraphs_or_compact_bullets",
                "clear_original_source_attribution",
                "traditional_chinese_highlight_disclosure",
            ],
            "tag_strategy": (
                "Use only people, organizations, subjects, and concepts supported "
                "by the selected segment."
            ),
        },
        ensure_ascii=False,
        indent=2,
    )
