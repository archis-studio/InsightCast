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

## Candidate Render Workflow

Use the API render endpoint only after the user explicitly asks to render a
candidate.

1. Do not start or stop the API server as part of rendering.
2. Reuse the existing analysis job ID from the completed analysis whenever it is
   still available in the running server process.
3. Queue only the requested candidate IDs:

   ```bash
   curl -sS -X POST \
     http://127.0.0.1:8765/api/v1/analysis-jobs/ANALYSIS_JOB_ID/renders \
     -H 'Content-Type: application/json' \
     -d '{"candidate_ids":["B"],"force_render":false}'
   ```

4. Poll render status with:

   ```bash
   curl -sS http://127.0.0.1:8765/api/v1/analysis-jobs/ANALYSIS_JOB_ID/renders
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
9. On failure, report the API error, inspect `stage-manifest.json`, and inspect
   the operation log for the failed stage and traceback.
