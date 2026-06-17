# Windowed Candidate Boundary Selection Design

## Problem

The current two-stage curator sends the complete transcript to both `topic_discovery`
and `candidate_boundary_selection`. For long videos, the second request can require
about 100k tokens by itself. With the current `gpt-5.4-mini` organization TPM limit
of 200k, that request often fails when any other OpenAI traffic is active.

The failure is not in source ingestion or transcription. Logs for the YouTube video
`uRQAhWZ1bxs` show repeated `source_cache_hit` and `transcript_cache_hit`, followed
by `LLM_REQUEST_FAILED` during `candidate_boundary_selection`.

## Goal

Reduce the token size of `candidate_boundary_selection` without deciding clip
boundaries from summaries. The boundary stage must still inspect original transcript
segments around the ranked topics, and final normalization must still align against
the complete transcript.

## Non-Goals

- Do not add rendering behavior.
- Do not change the YouTube analysis CLI contract.
- Do not replace topic discovery with chunk map-reduce in this iteration.
- Do not rely on lossy summaries for final candidate start and end times.
- Do not implement rate-limit-aware retry as part of this design. That remains a
  useful follow-up, but this work targets prompt size.

## Current Architecture

`CuratorEngine.curate()` runs:

1. `discover_topics()`: sends the full transcript and returns a ranked topic pool.
2. `select_candidates()`: sends the full transcript again plus the topic pool and
   returns final candidates.

`JobService._process_analysis()` records these as separate stages:

- `topic_discovery`
- `candidate_boundary_selection`

Candidate normalization already receives the complete transcript and aligns returned
times to transcript segment boundaries.

## Proposed Architecture

Keep full-transcript topic discovery. Change candidate boundary selection so it builds
a compact set of original transcript windows around the ranked topics, then sends only
those windows to the LLM.

Add an internal curator helper:

```python
def _build_topic_windows(
    *,
    segments: Sequence[TranscriptSegment],
    topics: Sequence[TopicDiscoveryOutput],
    target_min_duration_seconds: float,
    final_max_duration_seconds: float,
) -> list[TranscriptSegment]:
    ...
```

`select_candidates()` will use this helper to produce `windowed_segments` for
`curator.build_user_prompt()`. It will continue to pass the full `transcript` into
`_normalize_candidates()` and `_validate_candidates()`.

## Window Rules

For each topic in the ranked topic pool:

1. Start with `topic.start_seconds` and `topic.end_seconds`.
2. Add context:
   - `pre_buffer_seconds = max(120, target_min_duration_seconds / 4)`
   - `post_buffer_seconds = max(180, target_min_duration_seconds / 4)`
3. Ensure the window can contain at least `final_max_duration_seconds` when possible.
   If the buffered topic range is shorter, expand symmetrically around its midpoint
   until it reaches that duration or hits video boundaries.
4. Clamp windows to the transcript duration.
5. Merge overlapping or adjacent windows.
6. Return all transcript segments that overlap the merged windows, preserving original
   segment order and without duplicates.

The helper should use the topic pool already produced by `discover_topics()`. The
existing pool size is `candidate_count * TOPIC_POOL_MULTIPLIER`, so no extra topic
filtering is needed unless future code changes the topic response size.

## Prompt Contract

Update the candidate boundary prompt payload to make the transcript scope explicit.
The `transcript` field will contain original transcript segments selected from source
windows around ranked topics, not the complete transcript.

Add these fields to the candidate boundary prompt payload:

```json
{
  "transcript_scope": "selected_source_windows_around_ranked_topics",
  "transcript_is_complete": false
}
```

The system prompt should continue to require complete argument arcs and meaningful
conclusions. It should avoid implying that the transcript is complete.

## Quality Safeguards

The design preserves quality through these constraints:

- Topic discovery still evaluates the full transcript.
- Boundary selection sees original transcript segments, not summaries.
- Candidate normalization and validation still use the full transcript.
- Windows are expanded to fit the maximum final duration when possible.
- Overlapping windows are merged, so related adjacent topics keep surrounding context.

If the window builder returns no segments, `select_candidates()` should fall back to
the full transcript for that attempt and record the condition in code comments/tests.
This protects against malformed topic times without failing otherwise valid analyses.

If candidate validation fails because returned boundaries are too short, too long, or
not normalizable, the existing validation-feedback retry remains in place. The retry
should rebuild the same windowed transcript and include validation feedback as it does
today. Expanded-window retry is out of scope for this change.

## Data Flow

1. API queues analysis.
2. Source ingestion and transcription load from cache or create artifacts as today.
3. `topic_discovery` receives the full transcript and returns ranked topics.
4. `candidate_boundary_selection` builds source windows from topic ranges.
5. The boundary LLM receives topic metadata plus only windowed original transcript
   segments.
6. Returned candidates are normalized and validated against the complete transcript.
7. Successful analyses are persisted as `WAITING_SELECTION` with unchanged manifest
   and candidate artifact structure.

## Error Handling

Existing LLM failure behavior remains unchanged for this design. If the OpenAI request
still fails, the job records `LLM_REQUEST_FAILED` in the same manifest and log format.

Malformed topic times should not crash window construction. Non-finite or invalid
topic ranges should be skipped. If all topics are skipped, use the full transcript
fallback.

## Testing

Add focused unit coverage in `tests/unit/test_curator_engine.py`:

- Window building adds pre/post context and clamps to transcript duration.
- Windows expand to at least `final_max_duration_seconds` when the source transcript
  is long enough.
- Overlapping windows merge and return de-duplicated segments in original order.
- Invalid topic ranges are skipped.
- Full transcript fallback is used when no valid windowed segments can be built.
- `select_candidates()` sends fewer segments to the client for topic windows while
  still normalizing candidates against the complete transcript.
- Existing validation-feedback retry behavior still includes the windowed transcript.

Update prompt tests in `tests/unit/test_prompts.py` to verify the new transcript scope
fields.

## Acceptance Criteria

- The candidate boundary prompt no longer serializes the full transcript when valid
  topic windows exist.
- Candidate quality-critical normalization still uses full transcript segments.
- Existing analysis, manifest, and CLI output contracts remain unchanged.
- Unit tests cover window construction, fallback, and prompt contract changes.
- The real previously failing video can be retried and should reach either
  `WAITING_SELECTION` or a smaller LLM request failure with evidence that the second
  stage prompt used windowed segments.
