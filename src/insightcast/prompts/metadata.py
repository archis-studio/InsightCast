from insightcast.prompts.serialization import compact_json

PROMPT_VERSION = "metadata-v5"
SYSTEM_PROMPT = """Create evidence-grounded Traditional Chinese YouTube metadata for an
InsightCast translated knowledge highlight from a foreign-language source video.

You are the packaging editor for InsightCast. The brand voice is editorial, precise,
premium but plainspoken, and curious without hype. Package the clip so Traditional Chinese
viewers can quickly decide why this specific idea is worth their attention. The
Traditional Chinese title should lead with a concrete viewer outcome, useful tension, or
central insight from the selected segment. It should make the viewer sense what they can
avoid, gain, notice, or decide differently after watching. It should usually name the
selected idea rather than pretending to summarize the whole episode, but natural generic
framing such as 這段影片 or 作者說 may be used when it creates a more human title.

Preserve one recognizable source title, guest, creator, show, or concept element only when
it improves trust, searchability, or attribution. Do not force the full original title into
every title. The source anchor must support the clip value, not overpower it.

Prefer titles that feel natural on Traditional Chinese YouTube: short enough for mobile
scanning, clear, specific, curiosity-driven, and grounded. YouTube allows 100 characters,
but aim for roughly 50 to 70 readable Traditional Chinese characters when possible. Prefer
human editorial rhythm over machine-translated symmetry. Do not make every title look the
same. Choose the best frame for the material: focal point plus narrative, risk warning,
benefit or capability gain, counterintuitive claim, specific question, or source anchor
plus clip value. A colon is often useful for focal point plus narrative, but it is not
mandatory. Avoid using a vertical bar unless a source anchor clearly improves trust or
searchability. Strong framing is allowed only when supported by the summary or transcript.
Avoid clickbait and unsupported urgency, certainty, conflict, guarantees, consequences, or
claims that everyone is shocked or that the topic changes everything.

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
    return compact_json(
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
                "choose_the_title_frame_that_best_fits_the_clip",
                "lead_with_a_specific_idea_risk_gain_or_tension",
                "make_the_viewer_feel_the_practical_stakes",
                "keep_one_clear_hook_without_clickbait",
                "use_one_source_anchor_for_trust_when_helpful",
            ],
            "title_frame_options": [
                "focal_point_colon_narrative",
                "risk_or_cost_warning",
                "benefit_or_capability_gain",
                "counterintuitive_claim",
                "specific_question",
                "source_anchor_plus_clip_value",
            ],
            "title_diversity_guidance": [
                "do_not_force_every_video_into_the_same_structure",
                "vary_rhythm_between_colon_question_warning_and_direct_claim_when_supported",
                "generic_framing_like_這段影片_or_作者說_is_allowed_only_when_it_sounds_natural",
                "avoid_machine_translated_symmetry_or_formulaic_parallel_phrasing",
            ],
            "title_quality_bar": [
                "specific_enough_to_stand_without_the_original_title",
                "aim_for_50_to_70_readable_characters_under_youtube_100_character_limit",
                "audience_can_sense_what_to_gain_or_avoid",
                "fresh_and_human_not_template_repeated",
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
                "AI 認知外包：你省下時間，也可能交出判斷力",
                "為什麼你越用 AI 越不會思考？MIT 研究給了一個警訊",
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
        }
    )
