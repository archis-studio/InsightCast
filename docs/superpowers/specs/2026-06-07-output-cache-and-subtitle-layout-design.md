# Output Cache and Subtitle Layout Design

## Goal

Make analysis and render results easier to find while avoiding repeated YouTube
downloads and audio extraction. Update burned bilingual subtitles so Traditional
Chinese is the primary reading line. Make the default candidate count and
candidate duration range configurable without removing per-request API overrides.

## Configurable Candidate Defaults

The server exposes these environment variables:

```env
DEFAULT_CANDIDATE_COUNT=2
DEFAULT_MIN_DURATION_MINUTES=8
DEFAULT_MAX_DURATION_MINUTES=12
```

Validation rules:

- `DEFAULT_CANDIDATE_COUNT` is an integer from 1 through 26.
- Both duration values are positive numbers.
- `DEFAULT_MAX_DURATION_MINUTES` is greater than or equal to
  `DEFAULT_MIN_DURATION_MINUTES`.

Invalid defaults prevent application startup with a clear settings validation
error.

### API Override Semantics

`POST /api/v1/analysis-jobs` keeps these request fields:

- `candidate_count`
- `min_duration_minutes`
- `max_duration_minutes`

Each field becomes optional. Resolution is field-by-field:

1. A value explicitly supplied by the request is used.
2. An omitted field uses its configured server default.

Clients may override one field without resending all three. The final resolved
duration range is validated after request values and server defaults are merged.
For example, overriding only `min_duration_minutes` with a value above the
configured maximum returns HTTP 422.

Explicit JSON `null` is not accepted as an override. Clients must omit a field to
use its server default. This distinguishes accidental nulls from intentional
fallback behavior.

OpenAPI describes the fields as optional overrides and does not present fixed
schema defaults that could differ from the running server configuration. The
queued job stores the fully resolved values for diagnostics and reproducibility.

## Output Structure

New jobs use this layout:

```text
outputs/
  jobs/
    <timestamp>_<title>_<job-id>/
      analysis/
        transcript.json
        candidates.json
      renders/
        <timestamp>-<render-id>/
          candidate-a/
            <title>.a.zh-TW.srt
            <title>.a.bilingual.ass
            <title>.a.bilingual.burned.mp4
            <title>.a.youtube-metadata.json
      job_state.json
      pipeline.log

  source-cache/
    <youtube-video-id>/
      source.mp4
      audio.mp3
      metadata.json
```

Direct render jobs also live under `outputs/jobs/`. They retain their existing
single `render/` directory because they do not have analysis candidates.

Existing output directories are not migrated automatically. They remain valid
historical artifacts and can be moved or deleted manually.

## Job Directory Rules

Jobs remain independent and sort by creation time. The directory name continues
to include:

- creation timestamp
- sanitized YouTube title
- short job ID
- `direct` for direct-render jobs

The job directory does not contain a copied source video or audio file. Its
`job_state.json` records absolute paths to the shared cache artifacts.

Render batch directories include both timestamp and short render ID to avoid
collisions between batches created during the same second:

```text
<timestamp>-<render-id-prefix>
```

## Source Cache

### Identity

The normalized YouTube video ID is the cache key. Different watch, share, embed,
or Shorts URLs for the same video resolve to one cache entry.

### Contents

Each cache entry contains:

- `source.mp4`: downloaded source media
- `audio.mp3`: 16 kHz mono transcription audio
- `metadata.json`: stable, sanitized YouTube metadata

`metadata.json` contains only fields used by the product:

- video ID
- title
- description
- duration
- uploader
- upload date
- canonical webpage URL
- tags

It must not persist yt-dlp format lists, signed download URLs, cookies, request
headers, automatic-caption URL maps, or the full raw yt-dlp payload.

### Cache Hit

A cache hit requires:

- the directory matches the normalized video ID
- metadata parses successfully and has the same video ID
- source video exists and is non-empty
- audio exists and is non-empty

On a valid hit, ingestion skips metadata retrieval, video download, and audio
extraction. It logs `source_cache_hit` with the video ID and safe paths.

### Cache Miss and Repair

On a miss, ingestion writes into a temporary cache directory and atomically
promotes it only after metadata, source video, and audio are complete.

An incomplete or invalid entry is treated as a miss. It is replaced only after a
new complete entry is ready, preventing a failed download from destroying a
previously usable entry.

The MVP has one FIFO worker, so two pipeline jobs cannot write the same cache
entry concurrently. Atomic promotion still protects against process interruption.

### Cleanup

Cache entries do not expire automatically. Provide a command-line cleanup command
that supports:

```text
cast_cache list
cast_cache remove <youtube-video-id>
cast_cache clear
```

`list` reports video ID, title, source size, audio size, and last modification
time. `remove` deletes one validated cache entry. `clear` requires an explicit
`--yes` confirmation flag.

Cleanup only touches `outputs/source-cache/`. It never deletes job analysis,
renders, logs, or state files. Historical job state may reference removed cache
files; completed analysis and render outputs remain inspectable, but additional
renders requiring the source will fail with a structured missing-source error.

## Subtitle Layout

Burned bilingual ASS subtitles use:

1. Traditional Chinese on the upper subtitle line.
2. English on the lower subtitle line.

Styles:

| Language | Color | Font | Size | Vertical position |
| --- | --- | --- | --- | --- |
| Traditional Chinese | soft yellow `#FFE082` | `PingFang TC` | 46 | upper |
| English | white `#FFFFFF` | `Arial` | 44 | lower |

Both lines retain a dark outline and shadow for contrast. The bottom margin keeps
the English line inside title-safe space. The Chinese line has a larger bottom
margin so it appears above English without overlap.

ASS stores colors in BGR order. `#FFE082` is serialized with opaque ASS primary
color `&H0082E0FF`.

The standalone `zh-TW.srt` output remains Chinese-only and is unchanged.

## Subtitle Quality Guard

Before serialization, subtitle generation rejects:

- empty translations
- translations containing punctuation only
- missing or reordered segment IDs

This prevents artifacts such as standalone `.` or `？` subtitle events. It does
not attempt to rewrite transcription errors or terminology in this change.

## Runtime Flow

Analysis:

```text
normalize URL
  -> resolve video ID
  -> validate source cache
  -> cache hit or atomic cache creation
  -> create timestamped job directory
  -> transcribe cached audio
  -> curate candidates
  -> write analysis results under outputs/jobs/
```

Candidate render:

```text
load retained analysis state
  -> read source video from cache
  -> cut candidate clip
  -> translate subtitle segments
  -> reject invalid translation items
  -> serialize Chinese SRT and Chinese-over-English ASS
  -> burn subtitles
  -> generate sanitized YouTube metadata
```

## Error Handling

Add stable errors for:

- invalid or incomplete source cache when repair also fails
- source cache removed before a requested render
- invalid punctuation-only translation output
- cache cleanup targeting an invalid video ID or path outside the cache root

Logs include cache hit/miss/repair decisions, but never signed URLs or raw yt-dlp
metadata.

## Testing

Unit tests cover:

- candidate default settings, bounds, and duration relationship
- omitted API fields use configured defaults
- partial request overrides merge field-by-field
- explicit null and invalid merged ranges are rejected
- explicit request values override configured defaults
- job directories under `outputs/jobs/`
- cache paths keyed by normalized video ID
- cache hit skips yt-dlp and FFmpeg
- incomplete cache triggers safe repair
- failed repair preserves a valid previous entry
- sanitized metadata excludes raw and signed URLs
- render batch timestamp plus short ID naming
- cache list, remove, clear confirmation, and path containment
- Chinese ASS style before English
- soft-yellow ASS color encoding
- punctuation-only translations rejected

Service tests cover analysis and render using a shared cached source. A focused
media acceptance test burns representative subtitles and checks output dimensions,
duration, and visible line ordering.

## Scope Boundaries

This change does not:

- migrate historical output directories
- restore in-memory jobs after server restart
- automatically expire cache entries
- deduplicate transcript or candidate analysis
- correct transcription terminology
- implement a frontend cache browser
