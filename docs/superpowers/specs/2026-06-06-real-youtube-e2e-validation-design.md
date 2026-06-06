# Real YouTube End-to-End Validation Design

## Goal

Validate Insight Cast against real public English YouTube videos in two rounds:

1. A fast, lower-cost run using a 15-30 minute video and 3-5 minute candidates.
2. A product-default run using a long-form podcast and 8-12 minute candidates.

Both rounds must exercise the actual pipeline from YouTube URL ingestion through
transcription, candidate selection, Traditional Chinese translation, subtitle
generation, video rendering, and YouTube metadata generation.

## Confirmed First-Round Source

- URL: `https://www.youtube.com/watch?v=7zCsfe57tpU`
- Title: `Is this the only skill left?`
- Duration: 22 minutes 10 seconds
- Availability: public
- Source language: English
- Useful source characteristics: English automatic captions and chapter markers

The first-round analysis request uses:

```json
{
  "youtube_url": "https://www.youtube.com/watch?v=7zCsfe57tpU",
  "candidate_count": 2,
  "min_duration_minutes": 3,
  "max_duration_minutes": 5,
  "force_reanalyze": true
}
```

After candidate review, candidate `A` is rendered first. Candidate `B` is only
rendered if candidate `A` succeeds or if comparing candidate quality is needed.

## Second-Round Source

The second round uses a public English long-form podcast supplied after the first
round is accepted. It uses the product defaults:

```json
{
  "candidate_count": 2,
  "min_duration_minutes": 8,
  "max_duration_minutes": 12,
  "force_reanalyze": true
}
```

The source should be at least 45 minutes long, require no login, contain sustained
spoken English, and allow yt-dlp to retrieve both audio and video.

## Preflight

Before paid or long-running calls, verify:

- Settings load without exposing secret values.
- FFmpeg and ffprobe are available.
- FFmpeg includes libass support.
- yt-dlp can retrieve source metadata and usable media formats.
- The API starts and `/health`, `/docs`, and `/openapi.json` respond.
- The automated test suite and Ruff pass.
- Output and work directories have sufficient free space.

The current yt-dlp installation reports that no supported JavaScript runtime is
configured. This does not block the confirmed first source, but the warning must
be recorded in the acceptance report because future YouTube format extraction may
be incomplete or stop working.

## Minimal Runtime Improvements

### Render State Guard

Candidate rendering may only be queued when an analysis job is in
`WAITING_SELECTION`, `COMPLETED`, or `FAILED` with valid candidates and retained
analysis artifacts. Requests made during ingestion, transcription, or curation
must return a stable structured error instead of relying on missing in-memory
state or raising an internal exception.

### Stage Diagnostics

Every pipeline stage must write start, completion, and elapsed-time information
to `pipeline.log`. The required stages are:

- metadata retrieval
- video download
- audio extraction
- transcription
- candidate curation
- clip extraction
- subtitle translation
- subtitle serialization
- subtitle burn
- metadata generation

Logs must include safe artifact paths and media sizes where useful, but must not
contain API keys, authorization headers, signed media URLs, full prompts, or full
transcripts.

### Error Cleanup

Remove the duplicate candidate error-conversion statement in the analysis render
path. Preserve candidate-specific errors and successful artifacts during partial
batch failure.

### Persistence Boundary

This validation keeps analysis and rendering in the same server process. Restoring
transcripts and source metadata after restart is explicitly deferred, but must be
reported as a product limitation because a completed analysis currently cannot be
rendered after process restart.

## Execution Flow

1. Start one API process with the updated `.env`.
2. Submit the first-round analysis request.
3. Poll until `WAITING_SELECTION` or `FAILED`.
4. Inspect `transcript.json`, `candidates.json`, job state, and pipeline log.
5. Evaluate both candidates for duration, coherence, title, reason, summary, and
   timestamp alignment with the transcript.
6. Submit candidate `A` for rendering.
7. Poll until the render batch is `COMPLETED` or `FAILED`.
8. Inspect the generated SRT, ASS, burned MP4, and YouTube metadata JSON.
9. Probe the MP4 and inspect representative frames and subtitle timings.
10. Record first-round findings before selecting the second-round source.
11. Repeat with the long-form source and default 8-12 minute settings.

## Acceptance Criteria

### Analysis

- The downloaded source duration matches YouTube metadata within a small media
  container tolerance.
- The transcript is English, non-empty, ordered, and spans the source.
- Exactly two candidates are produced.
- Each candidate is inside the requested duration range and transcript bounds.
- Candidate timestamps correspond to complete, understandable idea arcs.
- Candidate title, reason, and summary accurately represent the selected excerpt.

### Rendering

- Candidate `A` produces a Traditional Chinese SRT, bilingual ASS, burned MP4,
  and YouTube metadata JSON.
- SRT and ASS entries are ordered, non-empty, and within clip duration.
- English and Traditional Chinese lines retain one-to-one segment mapping.
- The MP4 contains playable H.264 video and AAC audio.
- The rendered duration matches the selected range within normal encoding
  tolerance.
- Burned subtitles are visible, legible, synchronized, and not clipped outside
  the frame.
- Traditional Chinese wording is natural for a Taiwanese audience and preserves
  names and technical terms.
- Generated YouTube metadata is relevant and defaults to private visibility.

### Operational Behavior

- Status transitions are visible through the API.
- Failures return a stable error code, stage, message, and safe details.
- `pipeline.log` identifies the failing boundary without leaking secrets.
- Successful source and analysis artifacts remain available for another render in
  the same process.

## Stop Conditions

Stop the run before further paid calls when:

- YouTube download or media format selection fails.
- Transcription returns an unsupported language or unusable timestamps.
- The configured OpenAI-compatible endpoint does not support the required
  transcription or structured response API.
- Candidate validation fails after the existing retry.
- Subtitle translation loses one-to-one segment mapping.
- FFmpeg cannot encode H.264/AAC or burn ASS subtitles.

Diagnose the failing boundary and make one evidence-based correction before
restarting the affected stage or job.

## Deliverables

For each round, produce a concise acceptance report containing:

- source URL, title, and duration
- request settings and model configuration names
- stage durations
- candidate table with timestamps, duration, title, reason, and quality notes
- artifact paths and sizes
- subtitle and video quality findings
- errors, retries, and operational limitations
- recommended product or pipeline changes ranked by impact

Generated media, transcripts, and logs remain under ignored output directories
and are not committed.
