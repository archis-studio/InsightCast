# Insight Cast

Local-first AI video curation for long-form English YouTube content.

Insight Cast downloads a YouTube video, transcribes the English audio, finds the strongest
standalone highlight ranges, and renders bilingual clips with Traditional Chinese subtitles.

It is built for creators and operators who want a reproducible local pipeline instead of a hosted
black box.

## What it does

- Analyze long-form YouTube videos.
- Transcribe English audio with OpenAI Whisper or local faster-whisper.
- Select complete 8-12 minute highlight candidates with scores.
- Render selected candidates into:
  - `video.mp4`
  - `subtitles.zh-TW.srt`
  - `subtitles.bilingual.ass`
  - `youtube-metadata.json`
- Keep outputs, manifests, transcripts, render stages, and logs on disk.

## What it is not

- Not a hosted SaaS.
- Not a full YouTube uploader yet.
- Not a multi-user backend.
- Not a general video editor.
- Not designed for non-English source audio yet.

The API server keeps active jobs in the current process. If you restart the server, old job IDs are
not restored, but generated files under `outputs/` remain available.

## Requirements

- Python 3.13+
- [uv](https://docs.astral.sh/uv/)
- FFmpeg with ffprobe
- OpenAI API key
- Enough disk space for source videos and rendered clips

macOS:

```bash
brew install uv ffmpeg
```

Ubuntu / Debian:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
sudo apt update
sudo apt install -y ffmpeg
```

## Quick start

```bash
git clone https://github.com/archis-studio/InsightCast.git
cd InsightCast
cp .env.example .env
uv sync --extra dev
```

Edit `.env` and set:

```env
OPENAI_API_KEY=sk-...
```

Start the API server in one terminal:

```bash
uv run cast_api
```

Analyze a video from another terminal:

```bash
uv run cast_analyze "https://www.youtube.com/watch?v=VIDEO_ID"
```

When the CLI reaches `WAITING_SELECTION`, it prints candidates like `A` and `B` with scores.
Render the candidate you want:

```bash
uv run cast_render ANALYSIS_JOB_ID B --wait
```

The CLI prints the render directory and final artifact paths when the render completes.

## Typical operator flow

1. Keep `uv run cast_api` running.
2. Run `uv run cast_analyze "YOUTUBE_URL"`.
3. Compare candidate scores and summaries.
4. Render only the chosen candidate:

   ```bash
   uv run cast_render ANALYSIS_JOB_ID A --wait
   ```

5. Review:
   - `video.mp4`
   - `subtitles.zh-TW.srt`
   - `subtitles.bilingual.ass`
   - `youtube-metadata.json`
   - `stage-manifest.json`

To force a fresh analysis:

```bash
uv run cast_analyze --force "YOUTUBE_URL"
```

To force a fresh render:

```bash
uv run cast_render ANALYSIS_JOB_ID B --wait --force-render
```

## Configuration

Most settings live in `.env`. The common ones are:

| Variable | Default | Purpose |
| --- | --- | --- |
| `OPENAI_API_KEY` | required | OpenAI API key. Never commit this. |
| `API_HOST` | `127.0.0.1` | Server bind host. Use `0.0.0.0` in Docker. |
| `API_PORT` | `8765` | Server port. |
| `API_BASE_URL` | `http://127.0.0.1:8765` | CLI target API URL. |
| `OUTPUT_DIR` | `outputs` | Persistent videos, transcripts, renders, logs. |
| `WORK_DIR` | `.work` | Temporary pipeline workspace. |
| `LLM_MODEL` | `gpt-5.4-mini` | Default text model for curation, translation, metadata. |
| `TRANSCRIPTION_PROVIDER` | `openai` | `openai` or `local`. |
| `OPENAI_TRANSCRIPTION_MODEL` | `whisper-1` | OpenAI transcription model. |
| `VIDEO_MAX_HEIGHT` | `1080` | Maximum downloaded video height. |
| `VIDEO_CRF` | `18` | Render quality. Lower is larger and usually cleaner. |

See [.env.example](.env.example) for the full list.

## Local Whisper

OpenAI transcription is the default. To use local faster-whisper:

```bash
uv sync --extra dev --extra local-whisper
```

```env
TRANSCRIPTION_PROVIDER=local
WHISPER_MODEL_SIZE=large-v3
WHISPER_DEVICE=auto
```

Local transcription can be slower and needs local model storage. Curation, translation, and
metadata generation still use OpenAI-compatible text models.

## Output layout

Generated media is intentionally local and inspectable:

```text
outputs/
  videos/
    <video-id>_<title-slug>/
      video.json
      source/
      transcripts/
      analyses/
        <analysis-id>/
          candidates/
            A/
              candidate.json
              renders/
                <render-id>/
                  video.mp4
                  subtitles.zh-TW.srt
                  subtitles.bilingual.ass
                  youtube-metadata.json
                  manifest.json
                  stage-manifest.json
      logs/
```

Do not commit `outputs/`, `.work/`, or `.env`.

## API and docs

With the server running:

- API: <http://127.0.0.1:8765>
- Swagger UI: <http://127.0.0.1:8765/docs>
- Health: <http://127.0.0.1:8765/health>

The CLIs are the recommended operator interface:

```bash
uv run cast_analyze --help
uv run cast_render --help
uv run cast_cache --help
```

## Docker

```bash
docker build -t insightcast .
docker run --rm \
  --env-file .env \
  -e API_HOST=0.0.0.0 \
  -p 8765:8765 \
  -v "$PWD/outputs:/app/outputs" \
  insightcast
```

Then run CLI commands from the host with:

```env
API_BASE_URL=http://127.0.0.1:8765
```

## Development

```bash
uv sync --extra dev
uv run ruff check .
uv run pytest
```

After changing package code used by `cast_api`, update the local environment and restart the API
process:

```bash
uv pip install -e .
uv run cast_api
```

Use `uv sync --extra dev` when dependencies or `pyproject.toml` change.

## For AI agents

This repo is local-first. Treat the API server as user-managed process state.

Before analysis or rendering, verify the server:

```bash
curl -fsS http://127.0.0.1:8765/health
ps -axo pid,command | rg 'uv run cast_api|cast_api'
```

Rules:

- Do not print `.env`, `OPENAI_API_KEY`, or raw request headers.
- Do not start, stop, or restart `uv run cast_api` unless the user explicitly asks.
- Use `uv run cast_analyze "YOUTUBE_URL"` for analysis.
- Treat `WAITING_SELECTION` as successful analysis completion.
- Render only when the user asks:

  ```bash
  uv run cast_render ANALYSIS_JOB_ID B --wait
  ```

- Treat render `COMPLETED` and `manifest.json` `render_state=ready` as success.
- Report candidate IDs, scores, time ranges, artifact paths, and the operation log.
- Keep generated media, `.work/`, `outputs/`, and `.env` out of commits.

More detailed agent instructions are in [AGENTS.md](AGENTS.md).

## Troubleshooting

### Server is not reachable

Start the API in a separate terminal:

```bash
uv run cast_api
```

Then check:

```bash
curl -fsS http://127.0.0.1:8765/health
```

### `OPENAI_API_KEY` error

Create `.env` from `.env.example` and set a real key. Do not paste the key into issues, prompts, or
logs.

### `FFMPEG_NOT_AVAILABLE`

Install FFmpeg and confirm:

```bash
ffmpeg -version
ffprobe -version
```

### `YOUTUBE_DOWNLOAD_FAILED`

Check that the video is public, available in your region, and downloadable without login. Operation
logs live under the relevant video root in `outputs/videos/.../logs/`.

### `UNSUPPORTED_LANGUAGE`

Insight Cast currently supports English source audio only.

### Server restarted and `JOB_NOT_FOUND`

Job IDs are process-local. Re-run analysis in the current server process, or inspect persisted
results under `outputs/videos/`.

## License

MIT. See [LICENSE](LICENSE).
