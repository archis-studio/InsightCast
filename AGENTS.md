# Agent Instructions

## Operating Model

This repository is local-first. Treat the API server as user-managed process
state, and treat the repository CLIs as the canonical operator interface.

1. Work from the repository root.
2. Never print `.env`, `OPENAI_API_KEY`, or raw request headers containing
   secrets.
3. Do not start, stop, or restart `uv run cast_api` unless the user explicitly
   asks for server lifecycle work.
4. Before analysis or rendering, verify the user already has the API running:

   ```bash
   curl -fsS http://127.0.0.1:8765/health
   ps -axo pid,command | rg 'uv run cast_api|cast_api'
   ```

5. If a command fails because of network, uv cache, package download, YouTube,
   OpenAI, or other sandbox-restricted access, rerun the same necessary command
   with the required approval/escalation mechanism instead of inventing a
   different workflow.
6. Keep generated media, `.work/`, `outputs/`, and `.env` out of commits unless
   the user explicitly requests otherwise.

## CLI Responsibilities

### `uv run cast_api`

Runs the FastAPI server. It is configured by `.env` / environment variables and
does not expose operational CLI flags.

Common settings:

- `API_HOST`: server bind host. Use `127.0.0.1` for normal local runs.
- `API_PORT`: server bind port, default `8765`.
- `API_BASE_URL`: client-facing API URL used by CLIs, not the server bind.
- `OUTPUT_DIR`: persistent video, analysis, render, and log root.
- `WORK_DIR`: temporary pipeline workspace.
- `OPENAI_API_KEY`: required and secret.

### `uv run cast_analyze`

Analyzes a YouTube URL through the running API.

```bash
uv run cast_analyze [--verbose] [--force] "YOUTUBE_URL"
```

- `YOUTUBE_URL`: required YouTube watch, share, embed, or Shorts URL.
- `--verbose`: print complete JSON responses after successful API requests.
- `--force`: create a new analysis job instead of reusing the latest one for
  the URL in the current server process.

### `uv run cast_render`

Renders selected candidate IDs through the running API.

```bash
uv run cast_render ANALYSIS_JOB_ID B --wait
uv run cast_render ANALYSIS_JOB_ID A B --wait
uv run cast_render ANALYSIS_JOB_ID B --wait --force-render
```

- `ANALYSIS_JOB_ID`: required job ID from `cast_analyze`; valid only while the
  current server process still knows the job.
- `candidate_ids`: one or more candidate IDs, such as `A`, `B`, or `A B`.
- `--wait`: poll until the render batch completes or fails. Use this for normal
  operator work.
- `--force-render`: create a new render even if reusable artifacts already
  exist. Use only when the user explicitly wants a fresh render.

## YouTube Analysis Workflow

Use the repository CLI as the canonical way to analyze a YouTube URL.

1. Do not start or stop the API server as part of analysis.
2. Check that the user has separately run `uv run cast_api`.
3. From the repository root, run `uv run cast_analyze "<youtube-url>"`.
4. Add `--verbose` when raw API payloads are needed for diagnosis.
5. Treat `WAITING_SELECTION` as successful analysis completion.
6. Report candidate IDs, titles, time ranges, summaries, and source artifact paths.
7. Also report the video root, analysis ID and directory, whether transcript reuse
   occurred when the CLI or log makes it known, candidate directories, and the
   operation log path.
8. On failure, report the structured console error and inspect the referenced
   `pipeline.log` when available.
9. Do not queue renders unless the user explicitly requests rendering.
10. If the server is not reachable, report that `uv run cast_api` must be started
    in a separate terminal; do not start it yourself unless asked.

## Candidate Render Workflow

Use the repository CLI as the canonical way to render a candidate, only after
the user explicitly asks to render.

1. Do not start or stop the API server as part of rendering.
2. Reuse the existing analysis job ID from the completed analysis whenever it is
   still available in the running server process.
3. From the repository root, render only the requested candidate IDs:

   ```bash
   uv run cast_render ANALYSIS_JOB_ID B --wait
   ```

4. Use `--force-render` only when the user explicitly requests a fresh render:

   ```bash
   uv run cast_render ANALYSIS_JOB_ID B --wait --force-render
   ```

5. Treat `COMPLETED` as successful render completion. Confirm the candidate
   result has no error and that the render manifest says `render_state=ready`.
6. Report the render ID, output directory, manifest path, `video.mp4`, Traditional
   Chinese SRT, bilingual ASS, YouTube metadata, stage manifest, and operation
   log path.
7. Summarize stage status from `stage-manifest.json` or the render-list response,
   especially `cut_clip`, `translate_subtitles`, `write_subtitles`,
   `burn_subtitles`, `generate_metadata`, and `validate_render`.
8. Verify the rendered MP4 with `ffprobe` when available, and report duration and
   size.
9. On failure, report the CLI/API error, inspect `stage-manifest.json`, and inspect
   the operation log for the failed stage and traceback.
10. If the API returns `JOB_NOT_FOUND`, explain that analysis job IDs are
    process-local. Ask the user to rerun analysis on the current server process,
    or inspect persisted artifacts under `outputs/videos` if they only need old
    results.

## Reporting Checklist

For analysis success, include:

- Analysis job ID.
- Video root.
- Analysis ID and directory.
- Transcript ID/path and whether reuse is known.
- Candidate IDs, titles, time ranges, and summaries.
- Candidate directories.
- Operation log path.

For render success, include:

- Render ID and output directory.
- `manifest.json`.
- `stage-manifest.json`.
- `video.mp4`.
- `subtitles.zh-TW.srt`.
- `subtitles.bilingual.ass`.
- `youtube-metadata.json`.
- Operation log path.
- Stage status summary.
- `ffprobe` duration and size when available.
