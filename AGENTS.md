# Agent Instructions

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
