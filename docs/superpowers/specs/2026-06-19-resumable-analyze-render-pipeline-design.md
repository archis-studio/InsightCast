# Resumable Analyze And Render Pipeline Design

## Goal

Make Insight Cast reliable for the primary operator workflow: an AI assistant
receives a long-form YouTube URL, runs analysis, selects an 8-12 minute highlight,
renders the selected candidate, and reports exactly what happened.

The source video may be longer than one hour, but the rendered output remains a
short highlight. The system should prioritize automatic reuse and resume by
default, with explicit force flags when a fresh run is required.

## Current Behavior

Analysis and rendering already persist video-centric artifacts under
`outputs/videos/<video>/`. The CLI can analyze a URL and report candidates, while
candidate rendering is queued through the API.

The fragile area is candidate rendering. `candidate_clip_render` currently bundles
clip cutting, subtitle translation, subtitle file writing, and subtitle burn-in
into one large stage. Historical render failures show that the most common hard
failures are subtitle generation errors: model output omits subtitle items, changes
the expected one-to-one segment mapping, or returns unreadable translated text.

The operator-facing symptom is often just "render failed" or a long-running
`RENDERING` state. During a slow burn-in encode, artifacts may be actively growing
on disk while the API still reports `RENDERING`, which makes healthy work look
stalled.

## Design Principles

Default behavior is agent-friendly automatic resume and reuse:

- reuse completed transcript, analysis, candidate, subtitle, render, and metadata
  artifacts when they match the requested URL, candidate, and source fingerprint;
- resume from the latest safe checkpoint when a prior run stopped mid-pipeline;
- force fresh work only when the caller explicitly passes a force option.

Errors must be actionable. Every failure should identify the stage, error code,
retryability, resume strategy, retained artifacts, and the exact log or manifest
that explains the failure.

Quality gates are required before a render can become publishable. The system must
not mark a render `ready` unless the video, subtitle files, metadata, and subtitle
mapping validations all pass.

## Stage Model

Analysis and render jobs will expose structured stage state. A stage record
contains:

- `stage`: stable stage identifier;
- `status`: `queued`, `running`, `completed`, `failed`, or `skipped`;
- `started_at`, `completed_at`, and `elapsed_seconds`;
- `artifacts`: files produced or reused by the stage;
- `resume_strategy`: how a rerun will continue from this point;
- `error`: structured error data when the stage fails.

Candidate rendering is split into these stages:

1. `cut_clip`
2. `translate_subtitles`
3. `write_subtitles`
4. `burn_subtitles`
5. `generate_metadata`
6. `validate_render`

Analysis keeps its existing high-level stages, but each stage should also record
whether it performed fresh work or reused cached artifacts.

## Subtitle Translation Repair

`translate_subtitles` is the reliability center of the design. Translation runs in
validated batches and writes checkpoint artifacts for each successful batch.

For each batch:

1. translate with the normal prompt;
2. validate exact ordered segment IDs and readable translated text;
3. if validation fails, retry the same batch with a stricter repair prompt;
4. if it still fails, split the batch and recurse;
5. if a single segment still fails, use a single-segment repair prompt;
6. if repair is exhausted, fail the render with
   `SUBTITLE_REPAIR_EXHAUSTED`.

Successful batch translations are persisted before moving on. Reruns reuse valid
batch files and only retry missing or failed batches. A render may not silently
fall back to English or placeholder text in the default path; preserving output
quality is more important than producing a damaged render.

## Checkpoints And Resume

The render directory keeps a stage manifest in addition to the existing render
manifest. It records each stage's state and the artifacts it produced. The work
directory stores intermediate files that are useful for resume, such as the
unburned clip and translated subtitle batches.

Resume rules:

- completed `cut_clip` is reused when the source fingerprint, candidate ID, and
  start/end seconds match;
- completed subtitle batches are reused when their source segment IDs and prompt
  version match;
- subtitle files are reused when all expected translation batches are valid;
- burned video is reused only if subtitle files, source fingerprint, and render
  config match;
- metadata is reused only if the candidate summary, source metadata, and metadata
  prompt version match.

Force controls:

- `--force-reanalyze` creates a new analysis and bypasses reusable analysis
  outputs;
- `--force-render` creates a new render batch and bypasses a ready render;
- `--force-translate` redoes subtitle translation while allowing clip and burn
  stages to reuse or rerun normally;
- `--force-metadata` regenerates metadata.

## Error Handling

All pipeline errors use stable error codes and include structured details. Render
errors should distinguish at least:

- `SOURCE_CACHE_MISSING`: source video required for rendering is gone;
- `SUBTITLE_BATCH_INVALID`: a batch failed validation before repair is exhausted;
- `SUBTITLE_REPAIR_EXHAUSTED`: all repair attempts for a segment failed;
- `SUBTITLE_FILE_INVALID`: generated SRT or ASS failed validation;
- `VIDEO_RENDER_FAILED`: ffmpeg cut or burn failed;
- `RENDER_ARTIFACT_INVALID`: final artifacts are missing, empty, or inconsistent;
- `METADATA_GENERATION_FAILED`: YouTube metadata could not be produced.

Every error includes:

- `stage`;
- `retryable`;
- `resume_from`;
- `candidate_id` and `render_id` where relevant;
- source segment IDs and original text for subtitle failures;
- paths to the operation log, stage manifest, render manifest, and retained
  artifacts.

## Logging And CLI Output

File logs remain the detailed diagnostic record. Console and CLI output should be
concise but specific enough for an AI assistant to report progress.

Render progress examples:

```text
TRANSLATING_SUBTITLES: batch 7/18 completed, repaired=1, reused=6
REPAIRING_SUBTITLE: segment_id=tx-184 attempt=single_segment_repair
BURNING_SUBTITLES: writing video.mp4
VALIDATING_RENDER: video, srt, ass, metadata ready
```

Final CLI/API summaries report:

- whether the run was fresh, reused, resumed, or forced;
- analysis ID, render ID, and candidate ID;
- current or terminal stage;
- artifact paths;
- retry/repair counts;
- warnings;
- operation log and manifest paths;
- recommended next action on failure.

## Quality Gates

`validate_render` runs before the render manifest is marked `ready`.

Required checks:

- subtitle segment IDs exactly match the selected transcript segments;
- translated text is non-empty and readable;
- subtitle timings are non-negative and ordered;
- SRT and ASS files exist and are non-empty;
- burned video exists and is non-empty;
- metadata exists and contains required publishing fields;
- artifact paths are inside the expected render directory;
- render manifest references only artifacts that exist.

Warnings, such as unusually long subtitle lines or heavy repair counts, are stored
in the stage manifest and included in the final summary. Warnings do not block
publishability unless they indicate invalid output.

## Operator Workflow

The intended AI-assisted flow is:

1. user provides a URL;
2. assistant runs `cast_analyze <url>`;
3. CLI reuses or resumes existing artifacts when possible;
4. assistant reports candidates and analysis artifacts;
5. user selects candidate A or B;
6. assistant queues render;
7. render resumes or reuses work by default;
8. assistant reports final video, subtitles, metadata, manifests, log, and any
   quality warnings.

The assistant should not need to decide from scratch whether reuse is safe. The CLI
and API should make that decision from manifests and source fingerprints, then
report the decision explicitly.

## Testing

Unit tests cover:

- subtitle repair retries, splitting, and terminal single-segment failure;
- checkpoint reuse for completed subtitle batches;
- stage manifest transitions;
- force flag behavior;
- quality gate success and failure;
- structured error payloads.

Service tests cover:

- interrupted render resumes from translated batches;
- ready render is reused by default;
- `--force-render` creates a new render batch;
- failed subtitle repair retains useful diagnostics;
- long-source analysis still renders only the selected 8-12 minute candidate.

Acceptance tests cover a real or fixture-backed long-form URL through analyze,
candidate selection, render, artifact validation, and rerun reuse.

## Out Of Scope

This design does not render full one-hour videos. It also does not add manual
subtitle editing UI, upload scheduling, or distributed workers. Those can build on
the same stage manifest later.
