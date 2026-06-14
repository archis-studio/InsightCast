# Curator Candidate Duration Normalization Design

## Goal

Make candidate curation reliable when the language model returns approximate time
ranges that do not satisfy the requested duration. Preserve complete transcript
segments while targeting 8-12 minute candidates and allowing a bounded 7-13 minute
tolerance.

## Problem

The curator currently asks the language model to provide exact `start_seconds` and
`end_seconds`. The application validates that their difference is between 480 and
720 seconds, retries once with generic validation feedback, and then fails the
analysis if the second response is still invalid.

Structured output constrains field types but cannot express arithmetic constraints
between two fields. Language models are also unreliable at exact subtraction over a
long transcript. The result is a validly structured response whose candidate
durations fail application validation even when the selected content is useful.

## Duration Policy

- Target duration: 480-720 seconds (8-12 minutes).
- Accepted tolerance: 420-780 seconds (7-13 minutes).
- Complete transcript segments take precedence over hitting the target exactly.
- A candidate outside the accepted tolerance remains invalid.
- Candidates must remain continuous and within the transcript.
- Overlap between candidates remains allowed.

The tolerance is fixed at 60 seconds below and above the requested duration bounds
for this change. It is an internal curation policy, not a new API parameter.

## Architecture

The language model remains responsible for semantic selection:

- candidate topic and idea arc;
- approximate start and end;
- suggested title;
- selection reason;
- summary and optional score.

`CuratorEngine` becomes responsible for deterministic timing:

1. Receive parsed candidate output.
2. Normalize each approximate range to transcript segment boundaries.
3. Prefer a segment-aligned range within the target duration.
4. Accept a segment-aligned range within the tolerance when no target-range window
   can preserve the selected idea arc.
5. Validate the normalized candidates with the existing identity, count, text, and
   transcript-bound checks plus the accepted tolerance.
6. Retry the model only when deterministic normalization cannot produce valid
   candidates.

This keeps semantic judgment in the model and arithmetic constraints in application
code.

## Segment Normalization

Transcript segments are treated as ordered, indivisible intervals.

For each candidate:

1. Find the first segment that overlaps or follows the model's proposed start.
2. Find the last segment that overlaps or precedes the model's proposed end.
3. Use the selected segment indexes as the initial continuous window.
4. If the window is shorter than 480 seconds, expand one adjacent segment at a time.
   Choose the side whose segment boundary is closest to the model's original range.
   At transcript edges, expand on the available side.
5. Stop expansion at the first window in the 480-720 second target range.
6. If one whole-segment expansion crosses 720 seconds but remains at or below 780
   seconds, accept it rather than split the segment.
7. If the initial window is longer than 720 seconds, remove outer segments one at a
   time while retaining overlap with the model's selected range. Choose the removal
   that loses the least overlap with the original range.
8. Stop contraction at the first window in the target range.
9. If one whole-segment contraction falls below 480 seconds but remains at or above
   420 seconds, accept it rather than split a segment.
10. Reject normalization when no continuous segment-aligned window containing part
    of the model's selected range can fit within 420-780 seconds.

The normalized `start_seconds` is the first retained segment's start. The normalized
`end_seconds` is the last retained segment's end. All descriptive fields remain
unchanged.

Empty transcripts and model ranges that do not overlap any transcript segment cannot
be normalized.

## Prompt Contract

Increment the curator prompt version. The user prompt will include:

- target minimum and maximum seconds;
- accepted minimum and maximum seconds;
- an explicit instruction that times are approximate content selections;
- an explicit instruction to prefer complete idea arcs;
- validation feedback containing each invalid candidate's actual duration and the
  accepted range.

The model should still aim for the target range. The tolerance is a fallback for
segment preservation, not the preferred output.

## Validation And Errors

Validation runs after normalization.

The duration validator accepts 420-780 seconds. Error feedback reports:

- candidate ID;
- actual duration;
- target range;
- accepted tolerance.

Candidate count, ordered IDs, positive ranges, transcript bounds, and non-empty text
fields retain their current behavior.

If the first response cannot be normalized, the curator retries once with detailed
feedback. If the second response also cannot be normalized, the existing
`INVALID_LLM_OUTPUT` or `INSUFFICIENT_CANDIDATES` error remains the external result.

## Persistence And Compatibility

Only normalized candidate times are persisted to `candidates.json`,
`candidate.json`, manifests, and API responses. Rendering therefore requires no
changes and continues using persisted candidate bounds.

No API request or response schema changes are required. Existing clients continue to
request duration targets in minutes. Existing analyses remain immutable and are not
rewritten.

## Testing

Unit tests will cover:

- an already valid candidate aligned to segments;
- an undersized candidate expanded into the target range;
- expansion that exceeds 12 minutes but remains within the 13-minute tolerance;
- an oversized candidate contracted into the target range;
- contraction that falls below 8 minutes but remains within the 7-minute tolerance;
- transcript-start and transcript-end expansion;
- candidates whose proposed boundaries fall inside segments;
- no overlap with the transcript;
- no possible 7-13 minute continuous segment window;
- detailed retry feedback with actual and accepted durations;
- preservation of candidate metadata during normalization.

Existing curator, service, API, manifest, and render tests must remain green.

## Success Criteria

- The reproduced YouTube analysis does not fail solely because the model performs
  inaccurate duration arithmetic when a valid segment-aligned 7-13 minute window
  exists.
- Persisted candidate bounds align exactly with transcript segment boundaries.
- Candidate duration normally falls within 8-12 minutes and never falls outside
  7-13 minutes.
- Invalid or impossible selections still fail with structured diagnostics.
- No render is queued as part of analysis or verification unless separately
  requested.
