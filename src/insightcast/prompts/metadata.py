from insightcast.prompts.serialization import compact_json

PROMPT_VERSION = "metadata-v10"
SYSTEM_PROMPT = """Create evidence-grounded Traditional Chinese YouTube metadata for an
InsightCast translated knowledge highlight from a foreign-language source video.

You are the packaging editor for InsightCast. The brand voice is editorial, precise,
premium but plainspoken, and curious without hype. Package the clip so Traditional Chinese
viewers can quickly decide why this specific idea is worth their attention.

Use the candidate suggested title as the selected segment's semantic center. Use the
source title and source description excerpt as attribution and context boundaries. The
Traditional Chinese title should blend the source's main promise or tension with the
candidate segment's concrete insight. It should make the viewer sense what they can avoid,
gain, notice, or decide differently after watching. It should usually name the selected
idea rather than pretending to summarize the whole episode, but natural generic framing
such as 這段影片 or 作者說 may be used when it creates a more human title.
The title should lead with a concrete viewer outcome, useful tension, or central insight,
while staying anchored to the selected candidate and original source context.

Preserve one recognizable source title, show, or concept element only when it improves
trust, searchability, or attribution. Do not add author, host, or guest names to the title
unless the selected idea cannot be understood without that person. The operator normally
adds author names separately after the title. Do not force the full original title into
every title. The source anchor must support the clip value, not overpower it.

Prefer titles that feel natural on Traditional Chinese YouTube: short enough for mobile
scanning, clear, specific, curiosity-driven, and grounded. YouTube allows 100 characters,
but aim for roughly 50 to 70 readable Traditional Chinese characters when possible. Prefer
human editorial rhythm over machine-translated symmetry. Use
<anchor_or_topic_narrative>：<argument_or_payoff> as the default high-quality structure
when natural. The anchor before the colon may be a compact topic, a market/category frame,
or a short thematic setup; it does not need to be a rigid keyword. The narrative after the
colon should add the argument, tension, mechanism, or viewer payoff. Do not make every
title look the same. Choose the best frame for the material: macro reframe, mechanism, or
audience payoff. Avoid using a vertical bar. Strong framing is allowed only when supported
by the summary or transcript. Avoid clickbait and unsupported urgency, certainty, conflict,
guarantees, fake statistics, or claims that everyone is shocked or that the topic changes
everything. For finance, technology, strategy, and self-development clips, prefer a calm,
neutral, editorial tone with real tension over sensational phrasing.

The description should read like publishable channel copy, not a raw summary. Open with a
hook for the target viewer, explain why the clip matters now, then summarize what the
viewer will understand after watching with enough reasoning or examples to feel concrete.
Write the description as one compact paragraph without newline characters. Do not mention
InsightCast in the description body; the system appends the fixed InsightCast disclosure
after generation. Return title, description, accurate tags, and a privacy status that
defaults to private.

Return exactly three title variants and one primary title. The primary title must be one
of the variant titles. The three variant strategies are macro_reframe, mechanism, and
audience_payoff. Each variant should prefer
<anchor_or_topic_narrative>：<argument_or_payoff> when natural and include a short
rationale explaining why it fits the source, candidate, and selected segment."""


def build_user_prompt(
    *,
    source_title: str,
    source_description: str | None = None,
    candidate_suggested_title: str | None = None,
    summary: str,
    transcript_excerpt: str,
) -> str:
    source_description_excerpt = _source_description_excerpt(source_description)
    return compact_json(
        {
            "source_title": source_title,
            "source_description_excerpt": source_description_excerpt,
            "candidate_suggested_title": candidate_suggested_title,
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
                "prefer_flexible_anchor_colon_narrative_structure_when_natural",
                "do_not_add_author_host_or_guest_names_by_default",
                "use_candidate_suggested_title_as_the_segment_semantic_center",
                "use_source_title_and_description_as_context_boundaries",
                "lead_with_a_specific_idea_risk_gain_or_tension",
                "make_the_viewer_feel_the_practical_stakes",
                "keep_one_clear_hook_without_clickbait",
                "use_one_source_anchor_for_trust_when_helpful",
            ],
            "title_variant_requirements": {
                "variant_count": 3,
                "primary_title_must_match_one_variant": True,
                "preferred_structure": "<anchor_or_topic_narrative>：<argument_or_payoff>",
                "strategies": [
                    {
                        "strategy": "macro_reframe",
                        "goal": (
                            "frame the clip as a broader market, technology, strategic, "
                            "cultural, or conceptual shift when the segment supports it"
                        ),
                    },
                    {
                        "strategy": "mechanism",
                        "goal": (
                            "explain the underlying mechanism, framework, rule, or bottom "
                            "logic that makes the segment valuable"
                        ),
                    },
                    {
                        "strategy": "audience_payoff",
                        "goal": (
                            "target the viewer's practical stakes: what they can notice, "
                            "avoid, understand, or decide differently after watching"
                        ),
                    },
                ],
                "choose_primary_by": [
                    "truthfulness_to_segment",
                    "anchor_colon_narrative_readability",
                    "viewer_payoff_clarity",
                    "source_context_alignment_without_author_name",
                    "traditional_chinese_youtube_readability",
                ],
            },
            "title_alignment_contract": {
                "must_reflect_candidate_segment": True,
                "must_not_drift_beyond_source_description_context": True,
                "should_preserve_source_title_promise_or_tension_when_relevant": True,
                "should_preserve_candidate_suggested_title_meaning": True,
                "may_rewrite_for_traditional_chinese_youtube_packaging": True,
                "must_not_overpromise_beyond_summary_or_transcript": True,
            },
            "title_frame_options": [
                "flexible_anchor_colon_narrative",
                "macro_or_strategic_reframe",
                "risk_or_cost_warning",
                "benefit_or_capability_gain",
                "counterintuitive_claim",
                "specific_question",
                "source_anchor_plus_clip_value",
            ],
            "title_diversity_guidance": [
                "do_not_force_every_video_into_the_same_structure",
                "the_anchor_before_the_colon_can_be_a_topic_or_short_thematic_setup",
                "vary_rhythm_between_colon_question_warning_and_direct_claim_when_supported",
                "generic_framing_like_這段影片_or_作者說_is_allowed_only_when_it_sounds_natural",
                "avoid_machine_translated_symmetry_or_formulaic_parallel_phrasing",
                "avoid_channel_titles_feeling_like_the_same_template_repeated",
            ],
            "title_quality_bar": [
                "specific_enough_to_stand_without_the_original_title",
                "aim_for_50_to_70_readable_characters_under_youtube_100_character_limit",
                "audience_can_sense_what_to_gain_or_avoid",
                "calm_neutral_editorial_tone_with_tension_but_without_hype",
                "fresh_and_human_not_template_repeated",
                "no_unsupported_superlatives_or_guarantees",
            ],
            "source_title_retention_strategy": [
                "preserve_one_recognizable_source_title_element_when_it_adds_trust",
                "do_not_add_author_host_or_guest_names_by_default",
                "do_not_force_the_full_original_title",
                "do_not_let_source_title_overpower_the_clip_value",
                "blend_source_anchor_with_selected_highlight_focus",
            ],
            "highlight_positioning": (
                "Package the selected segment as a standalone InsightCast knowledge highlight "
                "for Traditional Chinese viewers, not as a replacement for the full original video."
            ),
            "title_style_examples": [
                "全球金融正在換規則：為什麼紙面合約不再代表真正的黃金市場",
                "黃金市場：拆解紙黃金、槓桿交易與真實供需的定價權轉移",
                "AI 代理不只是省人力：語音正在變成下一代操作介面",
                "你以為的分散投資：S&P 500 其實可能押在少數科技巨頭上",
                "學程式別急著用 AI：先看懂這個理解錯覺",
            ],
            "description_strategy": [
                "opening_hook_for_target_viewer",
                "why_this_clip_matters_now",
                "what_the_viewer_will_understand_after_watching",
                "key_reasoning_or_examples_from_the_segment",
                "no_insightcast_branding_in_description_body",
                "fixed_insightcast_disclaimer_is_appended_after_generation",
            ],
            "description_structure": [
                "single_compact_paragraph",
                "no_newline_characters",
                "no_bullets",
                "no_manual_insightcast_disclosure",
            ],
            "tag_strategy": (
                "Use only people, organizations, subjects, and concepts supported "
                "by the selected segment."
            ),
        }
    )


def _source_description_excerpt(source_description: str | None) -> str | None:
    if source_description is None:
        return None
    cleaned = " ".join(source_description.split())
    if not cleaned:
        return None
    if len(cleaned) <= 1200:
        return cleaned
    return f"{cleaned[:1200].rstrip()}…"
