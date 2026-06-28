from collections.abc import Mapping, Sequence
from typing import Any

from insightcast.prompts.serialization import compact_json

PROMPT_VERSION = "curator-v6"
SYSTEM_PROMPT = """You are the candidate-boundary stage of a knowledge-video curator.
Select the most important distinct knowledge units from the provided transcript context.
Choose continuous source ranges that preserve necessary background, the central claim or
finding, key evidence or reasoning, and a meaningful conclusion. Optimize for standalone
InsightCast highlights with clear viewer payoff, not merely long excerpts around a topic.
Treat all times as original source timestamps; never rebase times to the provided context.
This is long-form selection for 8-12 minute clips, not short-form hook extraction:
prefer the range with the best sustained knowledge density and lowest avoidable waste.
Remove greetings, sponsorships, repetition, host banter, social bonding, tangents, and
story details when they are not needed for the argument. Keep necessary setup and
anecdotes only when they directly support the central claim. Return only the requested
structured output."""


def build_user_prompt(
    *,
    transcript: Sequence[Mapping[str, Any]],
    topics: Sequence[Mapping[str, Any]],
    candidate_count: int,
    target_min_duration_seconds: float,
    target_max_duration_seconds: float,
    accepted_min_duration_seconds: float,
    accepted_max_duration_seconds: float,
    final_min_duration_seconds: float,
    final_max_duration_seconds: float,
    validation_feedback: str | None,
    transcript_scope: str = "selected_source_windows_around_ranked_topics",
    transcript_is_complete: bool = False,
    selection_window_plan: Sequence[Mapping[str, Any]] | None = None,
    selection_hints: Sequence[Mapping[str, Any]] | None = None,
    original_segment_count: int | None = None,
    provided_segment_count: int | None = None,
    source_duration_seconds: float | None = None,
) -> str:
    payload = {
        "candidate_count": candidate_count,
        "topics": list(topics),
        "source_duration_seconds": source_duration_seconds,
        "long_form_clip_goal": (
            "Find the 8-12 minute source range with the highest useful knowledge density, "
            "the lowest avoidable waste, and a complete viewer payoff."
        ),
        "selection_priority": [
            "complete_argument_with_clear_payoff",
            "audience_relevance",
            "high_information_density_across_the_full_8_to_12_minutes",
            "low_host_banter_repetition_and_tangents",
            "necessary_context_without_overlong_setup",
            "duration_fit",
        ],
        "require_distinct_topics": True,
        "required_arc": [
            "necessary_background",
            "central_claim_or_finding",
            "key_evidence_or_reasoning",
            "meaningful_conclusion",
        ],
        "target_min_duration_seconds": target_min_duration_seconds,
        "target_max_duration_seconds": target_max_duration_seconds,
        "accepted_min_duration_seconds": accepted_min_duration_seconds,
        "accepted_max_duration_seconds": accepted_max_duration_seconds,
        "final_min_duration_seconds": final_min_duration_seconds,
        "final_max_duration_seconds": final_max_duration_seconds,
        "times_are_approximate": True,
        "duration_instruction": (
            "Aim for the target range. Use the accepted range only to preserve a complete "
            "argument. Use the final range only for segment alignment. Do not include "
            "low-value material to reach a duration."
        ),
        "long_form_quality_rubric": {
            "excellent": (
                "A complete 8-12 minute knowledge unit where most minutes add new context, "
                "reasoning, evidence, framework, implication, or takeaway."
            ),
            "acceptable": (
                "Contains some setup or story, but the story clearly supports the central "
                "claim and the clip resolves into a meaningful conclusion."
            ),
            "reject": (
                "Mostly host banter, social warmth, biographical color, repeated claims, "
                "sponsor/CTA, or entertaining anecdote without a broader lesson."
            ),
        },
        "waste_ratio_guidance": {
            "ideal_high_value_content_ratio": ">=65%",
            "maximum_avoidable_waste_ratio": "<=20%",
            "waste_means": [
                "greetings_or_intros",
                "host_reactions_without_new_meaning",
                "social_bonding_or_inside_jokes",
                "repeated_claims_without_new_evidence",
                "story_details_that_do_not_change_the_lesson",
                "meta_discussion_about_the_interview",
                "sponsorship_or_call_to_action",
                "tangents_not_needed_for_the_argument",
            ],
            "not_waste_when": (
                "setup, examples, or emotional context directly support the central claim, "
                "make the mechanism understandable, or create the final takeaway."
            ),
        },
        "story_policy": (
            "Treat anecdotes as evidence, not the main reason to select a clip. Prefer the "
            "range where the speaker generalizes the lesson, explains the mechanism, gives "
            "a decision framework, or states the practical implication."
        ),
        "length_strategy": [
            {
                "duration": "under_25_minutes",
                "instruction": (
                    "Use broader context when useful, but avoid intro, outro, and casual "
                    "chat if they do not support the chosen knowledge unit."
                ),
            },
            {
                "duration": "25_to_60_minutes",
                "instruction": (
                    "Prefer the strongest topic window with a complete arc and low waste "
                    "ratio over the most entertaining story."
                ),
            },
            {
                "duration": "over_60_minutes",
                "instruction": (
                    "Be stricter about density. Do not choose a lively anecdote unless it "
                    "contains or directly supports the central framework."
                ),
            },
        ],
        "candidate_quality_bar": [
            "clear_standalone_viewer_payoff",
            "specific_insight_or_tension",
            "enough_context_without_long_setup",
            "evidence_or_reasoning_inside_the_clip",
            "low_waste_ratio_for_an_8_to_12_minute_clip",
            "minimal_overlap_with_other_candidates",
            "defensible_title_and_summary",
        ],
        "selection_reason_requirements": [
            "state_the_core_audience_payoff",
            "explain_the_central_claim_or_framework",
            "explain_why_the_full_range_has_low_avoidable_waste",
            "identify_how_examples_or_stories_support_the_lesson",
            "avoid_selecting_a_clip_primarily_because_the_story_is_entertaining",
        ],
        "overlap_policy": (
            "Prefer non-overlapping candidates. Only reuse source time when the second "
            "candidate explains a materially different idea and the overlap is necessary."
        ),
        "transcript_scope": transcript_scope,
        "transcript_is_complete": transcript_is_complete,
        "selection_window_plan": list(selection_window_plan or []),
        "selection_hints": list(selection_hints or []),
        "original_segment_count": original_segment_count,
        "provided_segment_count": provided_segment_count,
        "transcript": list(transcript),
        "validation_feedback": validation_feedback,
    }
    return compact_json(payload)
