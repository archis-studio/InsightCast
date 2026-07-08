# Agent Instructions

This repository is local-first. Treat the API server as user-managed process
state, and treat repository CLIs as the canonical operator interface.

## Operating Rules

1. Work from the repository root.
2. Never print `.env`, `OPENAI_API_KEY`, or raw request headers containing
   secrets.
3. Do not start, stop, or restart `uv run cast_api` unless the user explicitly
   asks for server lifecycle work.
   Do not start or stop the API server for analysis/render tasks.
4. Keep generated media, `.work/`, `outputs/`, and `.env` out of commits unless
   the user explicitly requests otherwise.
5. If a command fails because of network, uv cache, package download, YouTube,
   OpenAI, or another sandbox-restricted dependency, rerun the same necessary
   command with the required approval/escalation mechanism.

## Server Check

Before analysis or rendering, verify the user already has the API running:

```bash
curl -fsS http://127.0.0.1:8765/health
ps -axo pid,command | rg 'uv run cast_api|cast_api'
```

If the server is not reachable, tell the user to start `uv run cast_api` in a
separate terminal. Do not start it yourself unless asked.

## Analysis Workflow

Use the CLI:

```bash
uv run cast_analyze "YOUTUBE_URL"
uv run cast_analyze "<youtube-url>"
```

Use `--force` only when the user asks for a fresh analysis. Use `--verbose` when
raw API payloads are needed for diagnosis.

Treat `WAITING_SELECTION` as successful analysis completion. Report:

- Analysis job ID.
- Video root; report as video root in summaries.
- Analysis ID and directory; report as analysis ID in summaries.
- Transcript path and known transcript reuse status.
- Candidate IDs, titles, time ranges, and summaries.
- Candidate directories; report as candidate directories in summaries.
- Operation log path.

Do not queue renders unless the user explicitly requests rendering.

## Render Workflow

Render only requested candidate IDs:

```bash
uv run cast_render ANALYSIS_JOB_ID B --wait
```

Use `--force-render` only when the user explicitly requests a fresh render.

Treat `COMPLETED` as successful render completion only after confirming the
candidate result has no error and the render manifest says `render_state=ready`.
Report:

- Render ID and output directory.
- `manifest.json`.
- `stage-manifest.json`.
- `video.mp4`.
- `subtitles.zh-TW.srt`.
- `subtitles.bilingual.ass`.
- `youtube-metadata.json`.
- Operation log path.
- Stage status summary.
- `ffprobe` duration and file size when available.

If the API returns `JOB_NOT_FOUND`, explain that analysis job IDs are
process-local. If the prior output includes `video_id` and `analysis_id`, retry
once with:

```bash
uv run cast_render ANALYSIS_JOB_ID CANDIDATE --video-id VIDEO_ID --analysis-id ANALYSIS_ID
```

This recovery path reports matching ready persisted renders; it does not queue
new render work.

## Failure Handling

On analysis or render failure:

- Report the structured CLI/API error.
- Inspect the referenced operation log or `pipeline.log` when available.
- For render failures, inspect `stage-manifest.json` and identify the failed
  stage.
- Do not invent alternate workflows when the canonical CLI gives a clear
  recovery path.

## Source Of Truth

- Human onboarding and examples: `README.md`.
- Environment settings: `.env.example`.
- CLI details: `uv run cast_analyze --help`, `uv run cast_render --help`,
  `uv run cast_cache --help`.
- Runtime API shape: OpenAPI at `/docs` when `cast_api` is running.
