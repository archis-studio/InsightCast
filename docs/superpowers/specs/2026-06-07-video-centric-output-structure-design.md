# Video-Centric Output Structure Design

## Goal

Replace the job-centric output layout with a video-centric structure that makes
source media, analysis history, candidate renders, direct renders, and future
YouTube upload state easy for both users and software agents to locate.

The stable identity is the normalized YouTube video ID. Different watch, share,
embed, and Shorts URLs for the same video resolve to one video directory and one
validated local source.

The design must support:

- reuse of an existing local source without downloading it again
- reuse of a transcript only when its source and transcription settings match
- immutable analysis history
- immutable render history
- visible and stable candidate labels such as `A`, `B`, and `C`
- direct renders under a clearly named `custom` location
- future YouTube uploads without copying render artifacts
- disk-based artifact discovery after an API server restart
- clear README instructions for finding every generated result

## Scope

This change includes:

- a new `outputs/videos/` hierarchy
- video, source, transcript, analysis, candidate, render, and publish manifests
- source and transcript cache lookup within each video directory
- output path construction and artifact discovery services
- job and API responses updated to expose the new paths
- disk-based discovery of publish-ready render artifacts
- documentation and tests for the new layout

This change does not:

- migrate existing output directories
- read existing job-centric output directories
- automatically delete old output directories
- implement YouTube OAuth or perform an actual upload
- deduplicate render files across different render versions
- add automatic retention or pruning policies

Existing directories such as `outputs/<timestamp>_<title>_<job-id>/` and
`outputs/jobs/` are legacy data. They remain untouched and may be deleted
manually.

## Chosen Approach

Use one self-contained directory per YouTube video, with immutable analysis and
render runs nested below it.

This is preferred over retaining separate video and job trees because a user
should not need a manifest join to find a candidate render. It is also preferred
over a global content-addressed store because the additional indirection and
garbage-collection requirements are unnecessary at the current scale.

## Canonical Layout

```text
outputs/
  videos/
    <video-id>_<title-slug>/
      video.json

      source/
        source.mp4
        audio.mp3
        manifest.json

      transcripts/
        <transcript-id>/
          transcript.json
          manifest.json

      analyses/
        <analysis-id>/
          manifest.json
          candidates.json
          candidates/
            A/
              candidate.json
              renders/
                <render-id>/
                  manifest.json
                  video.mp4
                  subtitles.zh-TW.srt
                  subtitles.bilingual.ass
                  youtube-metadata.json
            B/
              candidate.json
              renders/
                <render-id>/
                  ...

      renders/
        custom/
          <render-id>/
            manifest.json
            video.mp4
            subtitles.zh-TW.srt
            subtitles.bilingual.ass
            youtube-metadata.json

      logs/
        <operation-id>.log
```

There is no new `legacy/` directory. Old outputs remain exactly where they are
and are outside the new storage contract.

## Video Identity And Directory Naming

The YouTube video ID is the canonical identity and lookup key. The title slug is
only a human-readable suffix.

```text
<video-id>_<title-slug>
```

Path lookup must first match the exact video ID prefix. It must not depend on the
current title or assume the title remains unchanged. A video directory is not
renamed when later metadata has a different title, because renaming would break
bookmarks and paths stored outside the application.

At most one managed directory may exist for a video ID. If multiple matching
directories are found, discovery fails with a structured storage-conflict error
instead of choosing one arbitrarily.

`video.json` contains:

- schema version
- video ID
- original and normalized YouTube URLs
- title
- uploader or channel when available
- source upload date when available
- first-seen and last-seen timestamps
- the relative source manifest path

## Source Reuse And Repair

The source directory replaces the global `source-cache/<video-id>/` entry. Before
downloading, ingestion:

1. extracts and validates the video ID
2. finds or creates the video root
3. reads `source/manifest.json`
4. verifies the declared files exist and have the expected nonzero sizes
5. verifies the stored source fingerprint
6. reuses the source when validation succeeds

The source fingerprint is a SHA-256 digest of `source.mp4`. The manifest also
stores file sizes and creation time. Hash validation is authoritative; size
checks provide an inexpensive early rejection.

On a miss or invalid source, ingestion downloads and extracts audio in a
temporary sibling directory. It writes the source manifest and atomically
promotes the complete directory. A failed repair must not replace a previously
valid source.

`source/manifest.json` contains:

- schema version
- video ID
- source fingerprint and algorithm
- relative paths and sizes for source video and transcription audio
- download and audio-extraction timestamps
- source metadata relevant to reproducibility
- state: `ready` or `invalid`

## Transcript Cache

A transcript is reusable only when all inputs that can affect its content match:

- source fingerprint
- transcription provider
- transcription model
- requested language
- transcript format or schema version

These values produce a deterministic cache key. `transcript-id` is a readable
short prefix of that key with enough entropy to avoid practical collisions.
Discovery still compares the complete key stored in the manifest.

Each transcript directory is immutable after it reaches `ready`. If its manifest
or transcript file is missing, invalid, or inconsistent, it is not reused. A new
transcript directory is created instead of overwriting the invalid one.

`transcripts/<transcript-id>/manifest.json` contains:

- schema version
- transcript ID and complete cache key
- source fingerprint
- provider, model, and language
- transcript relative path
- creation time
- state: `ready` or `failed`
- structured error details when failed

## Analysis Runs

Every analysis request that is not returned from the existing in-process request
deduplication creates a new immutable analysis run, including forced reanalysis
and analyses that use an existing transcript.

An `analysis-id` combines a UTC timestamp and a short unique ID:

```text
<YYYYMMDD-HHMMSS>-<short-id>
```

The analysis manifest records:

- schema version
- analysis ID and operation ID
- creation and completion timestamps
- normalized source URL and video ID
- transcript ID
- curator model and prompt version
- candidate count and duration bounds
- state: `queued`, `running`, `waiting-selection`, `completed`, or `failed`
- relative paths to `candidates.json` and candidate directories
- structured error details when failed

`candidates.json` preserves the complete ordered curator response. Each candidate
also gets a dedicated `candidates/<candidate-id>/candidate.json` file so users
and agents can inspect one candidate without parsing the aggregate file.

Candidate IDs are normalized to uppercase and must be safe single directory
segments. The initial product contract uses `A` through `Z`. Candidate identity
is scoped to one analysis run; candidate `A` from two analyses is not the same
selection.

## Candidate Renders

Candidate renders live below the candidate that produced them:

```text
analyses/<analysis-id>/candidates/A/renders/<render-id>/
```

This is the primary human navigation rule: choose the analysis, choose the
original candidate letter, then choose a render version.

Every render request creates a new immutable render directory when rendering is
actually required. An API request that reuses an already completed candidate
returns the existing render manifest and does not create an empty render
directory.

A `render-id` uses the same timestamp plus short unique ID convention as an
analysis ID. Stable artifact names avoid repeating the source title and candidate
letter at every path level:

- `video.mp4`
- `subtitles.zh-TW.srt`
- `subtitles.bilingual.ass`
- `youtube-metadata.json`
- `manifest.json`

The render manifest contains:

- schema version
- render ID and operation ID
- analysis ID
- candidate ID
- selected start and end seconds
- source fingerprint and transcript ID
- render configuration relevant to reproducibility
- relative artifact paths, sizes, and optional hashes
- creation and completion timestamps
- render state: `queued`, `rendering`, `ready`, or `failed`
- publish state: `not-uploaded`, `uploading`, `uploaded`, or `upload-failed`
- YouTube upload video ID, URL, and timestamps when available
- structured render or upload error details

A render is publishable only when render state is `ready`, the video and metadata
files exist, and their paths remain within the render directory.

## Direct Renders

Direct time-range renders are video-level custom renders:

```text
renders/custom/<render-id>/
```

They use the same stable artifact names and render manifest contract as candidate
renders. Their manifest omits analysis and candidate IDs and instead records
`kind: custom` with the requested start and end seconds.

Custom renders do not consume candidate letters. This keeps `A`, `B`, and `C`
meaningful as curator outputs from a specific analysis.

## Logs And Temporary Work

Persistent logs live under:

```text
logs/<operation-id>.log
```

Analysis, candidate render, direct render, and later upload operations each use
an operation ID. Their manifests include the relative log path.

Temporary media remains under configurable `WORK_DIR`, grouped by video ID and
operation ID. Successful operations remove disposable unburned clips. Failed
operations retain temporary files for diagnosis. Temporary paths are not part of
the persistent artifact contract.

## Manifest Path Rules

All persisted manifest paths are relative to the manifest's owning video root or
render directory, as defined by the individual schema. Absolute paths are
resolved only at runtime and may still be returned by the local API for
convenience.

Resolvers must reject:

- absolute paths in persisted artifact fields
- `..` traversal outside the owning root
- symlinks that resolve outside the owning root
- duplicate video directories for one video ID
- unsupported future schema versions

JSON writes use a sibling temporary file, flush and fsync, then atomic replace.
State changes that make an artifact discoverable, especially `ready` and
`uploaded`, occur only after required files are durably present.

## Discovery And Restart Behavior

Filesystem manifests become the durable source of artifact discovery. The
in-memory registry may continue to coordinate active work, but completed source,
transcript, analysis, and render lookup must not depend on the server process
that created them.

Storage services provide explicit operations for:

- find a video root by video ID
- validate or repair its source
- find a matching ready transcript by cache key
- list analyses newest first
- load one analysis and its candidates
- list renders for a candidate newest first
- list custom renders newest first
- resolve one publishable render by render ID
- list all publishable or uploaded renders for a video

Ordering uses manifest timestamps and IDs, not filesystem modification time.
There is no ambiguous implicit "latest upload target" operation. Upload APIs must
eventually accept an explicit render ID; listing endpoints or CLI output can help
the caller choose it.

## Runtime Flow

Analysis:

```text
normalize URL and extract video ID
  -> find or create video root
  -> validate and reuse source, or atomically download and repair
  -> calculate transcript cache key
  -> reuse matching ready transcript, or create a new transcript
  -> create immutable analysis directory
  -> curate candidates
  -> write aggregate and per-candidate JSON
  -> mark analysis waiting-selection
```

Candidate render:

```text
load analysis and candidate manifest
  -> validate source and transcript references
  -> create candidate renders/<render-id> directory
  -> render stable artifact filenames
  -> generate YouTube metadata
  -> validate required files
  -> atomically mark render ready and not-uploaded
```

Direct render:

```text
resolve video and source
  -> resolve or create transcript
  -> create renders/custom/<render-id>
  -> render requested time range
  -> generate metadata
  -> atomically mark render ready and not-uploaded
```

Future upload:

```text
resolve explicit render ID from disk
  -> validate publishable artifacts
  -> mark uploading
  -> upload to YouTube
  -> mark uploaded with remote video ID and URL
     or mark upload-failed with retryable diagnostics
```

## Error Handling

Add structured errors for:

- duplicate video roots for one video ID
- invalid or unsupported manifest schema
- source fingerprint mismatch
- source repair failure
- transcript cache entry corruption
- missing analysis or candidate manifest
- invalid candidate directory ID
- render artifact missing or outside its owning directory
- render not ready for publishing
- render ID not found or ambiguous
- invalid publish state transition

Failures update the nearest owning manifest when possible and preserve its log
path. A partial render directory is retained with render state `failed`; it must
not be returned by publishable-render discovery.

## README Requirements

README changes are a required part of implementation, not follow-up
documentation. The output section must include:

- the complete canonical directory tree
- a lookup table for source, transcript, analysis, candidate, render, metadata,
  log, and custom-render paths
- an explanation that video ID, not URL spelling or title, determines reuse
- source validation and repair behavior
- the transcript cache-key inputs
- how to find candidate `A`, `B`, or `C` from a chosen analysis
- how to list render versions and identify a render ID
- how to recognize a publishable render from its manifest
- how future upload status is stored without copying files
- the `renders/custom/` convention for direct renders
- a notice that old output directories are deprecated, ignored, and not
  migrated
- shell examples using `find`, `jq`, or the repository CLI to locate common
  artifacts

Examples must use paths from the final implementation rather than conceptual
placeholders that differ from runtime output.

## Testing

Unit and service tests cover:

- all supported YouTube URL variants resolving to one video ID and directory
- title changes not creating or renaming a video directory
- duplicate video root detection
- source hit, miss, validation, atomic repair, and failed repair preservation
- source SHA-256 fingerprint persistence and mismatch handling
- transcript cache hits for identical inputs
- transcript cache misses after provider, model, language, schema, or source
  fingerprint changes
- immutable transcript, analysis, and render directories
- aggregate and per-candidate analysis files
- uppercase `A`, `B`, and `C` directory paths
- candidate IDs scoped to an analysis
- render reuse returning an existing directory without creating an empty one
- custom render paths
- stable artifact filenames
- relative-path serialization and containment checks
- render and publish state transitions
- failed and partial renders excluded from publishable discovery
- artifact discovery after constructing a fresh service instance
- explicit render-ID upload resolution
- old job-centric output directories being ignored
- README and repository contract assertions for the documented layout

Verification includes the full unit and service test suite, Ruff, and a focused
filesystem acceptance test that creates a video, reuses its source and
transcript, runs two analyses, renders candidate `A` twice, and resolves one
render as publishable from a newly constructed service.

## Compatibility And Rollout

The new layout is a clean break for generated output:

- new work writes only beneath `outputs/videos/`
- runtime discovery reads only managed `outputs/videos/` manifests
- existing `outputs/jobs/`, `outputs/source-cache/`, and root-level job
  directories remain untouched
- no startup migration or fallback reader is added
- users may manually delete legacy data after confirming it is no longer needed

API response fields should retain their semantic meaning where practical, but
absolute artifact paths will point to the new hierarchy. Any endpoint that
implicitly selects a render for upload must be replaced or extended with an
explicit render identifier before real upload support is implemented.
