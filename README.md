# Insight Cast

Insight Cast 是 local-first 的 AI 影片精華剪輯 pipeline，主要用來處理英文 YouTube 長影片：下載來源、轉錄語音、挑選可獨立觀看的精華片段、產出繁體中文與雙語字幕影片，並把所有輸出、manifest、log 留在本機。

請在下載、剪輯或發布任何來源影片前，確認你有權使用該內容，並遵守 YouTube 條款與著作權規範。

## 產品定位

目前可以：

- 分析英文 YouTube 影片，挑出有完整上下文的精華片段。
- 使用 OpenAI Whisper 或本機 faster-whisper 轉錄。
- 剪出片段並燒錄繁體中文 / 雙語字幕。
- 產出 YouTube metadata 草稿。
- 將影片、字幕、transcript、manifest、stage-manifest、operation log 保存在 `outputs/`。
- 以 CLI 作為主要操作介面，FastAPI 作為本機服務。

目前不是：

- 不是雲端 SaaS。
- 不是多人後台。
- 不是完整影片剪輯軟體。
- 還不是完整 YouTube uploader。

## 系統需求

- Python 3.13+
- [uv](https://docs.astral.sh/uv/)
- FFmpeg / ffprobe
- OpenAI API key
- 足夠磁碟空間存放 source video、transcript、render output

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

## 快速開始

```bash
git clone https://github.com/archis-studio/InsightCast.git
cd InsightCast
cp .env.example .env
uv sync --extra dev
```

編輯 `.env`，至少設定：

```env
OPENAI_API_KEY=sk-...
```

在第一個 terminal 啟動 API：

```bash
uv run cast_api
```

在第二個 terminal 分析影片：

```bash
uv run cast_analyze "https://www.youtube.com/watch?v=VIDEO_ID"
```

當 CLI 顯示 `WAITING_SELECTION`，代表分析完成，會列出候選片段，例如 `A`、`B`。

Render 其中一個候選：

```bash
uv run cast_render ANALYSIS_JOB_ID B --wait
```

CLI 會在完成後列出 render directory、影片、字幕、metadata、manifest 與 log path。

## 手動分析與 Render 範例

這是一般使用者最常用的完整操作流程。

1. 啟動 API，並讓它持續跑著：

   ```bash
   uv run cast_api
   ```

2. 在第二個 terminal 確認 API ready：

   ```bash
   curl -fsS http://127.0.0.1:8765/health
   ```

3. 分析 YouTube 影片：

   ```bash
   uv run cast_analyze "https://youtu.be/VIDEO_ID"
   ```

4. 從 CLI 輸出挑選候選片段。分析完成時會看到類似：

   ```text
   analysis_job_id: 123abc...
   status: WAITING_SELECTION
   candidates:
     A  00:12:30 -> 00:22:10  Suggested title...
     B  00:34:05 -> 00:43:40  Suggested title...
   ```

5. Render 想要的候選：

   ```bash
   uv run cast_render 123abc... A --wait
   ```

6. 到 CLI 印出的 output directory 檢查結果。重點檔案：

   ```text
   video.mp4
   subtitles.zh-TW.srt
   subtitles.bilingual.ass
   youtube-metadata.json
   manifest.json
   stage-manifest.json
   ```

常用變體：

```bash
uv run cast_analyze --force "https://youtu.be/VIDEO_ID"
uv run cast_render ANALYSIS_JOB_ID A --wait --force-render
uv run cast_analyze --verbose "https://youtu.be/VIDEO_ID"
```

`ANALYSIS_JOB_ID` 只存在目前 API process。若 server 重啟，請在目前 process 重新分析，或直接查看 `outputs/videos/` 裡已保存的輸出。

## 設定

設定放在 `.env`；完整可用項目以 [.env.example](.env.example) 為準。

常用設定：

| 變數 | 預設值 | 說明 |
| --- | --- | --- |
| `OPENAI_API_KEY` | required | OpenAI API key。不要 commit。 |
| `API_BASE_URL` | `http://127.0.0.1:8765` | CLI 連線的 API URL。 |
| `OUTPUT_DIR` | `outputs` | 永久輸出：影片、字幕、transcript、manifest、log。 |
| `WORK_DIR` | `.work` | pipeline 暫存目錄。 |
| `LLM_MODEL` | `gpt-5.4-mini` | 分析、翻譯、metadata 預設文字模型。 |
| `LLM_CAPABILITY_PROFILE` | `openai_strict` | `local_conservative` 會使用較小字幕翻譯批次，較適合本地或 OpenAI-compatible 模型。 |
| `TRANSCRIPTION_PROVIDER` | `openai` | `openai` 或 `local`。 |
| `VIDEO_MAX_HEIGHT` | `1080` | 下載影片高度上限。 |
| `VIDEO_CRF` | `18` | Render 品質。數字越低通常檔案越大。 |
| `SUBTITLE_TIMING_NORMALIZATION` | `true` | 對字幕時間做保守微調；可用 offset、最短顯示、最大延伸與最小間隔設定調整。 |

## 本機 Whisper

預設使用 OpenAI transcription。若要改用本機 faster-whisper：

```bash
uv sync --extra dev --extra local-whisper
```

```env
TRANSCRIPTION_PROVIDER=local
WHISPER_MODEL_SIZE=large-v3
WHISPER_DEVICE=auto
```

本機轉錄可能較慢，也需要下載模型。內容分析、翻譯與 metadata 仍會使用設定的 OpenAI-compatible 文字模型。

## 輸出結構

Insight Cast 以 video-centric layout 保存輸出：

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
      renders/
        custom/
      logs/
```

常用查找路徑：

- Candidate render: `outputs/videos/<video-id>_<title-slug>/analyses/<analysis-id>/candidates/A/renders/<render-id>/`
- Direct render: `outputs/videos/<video-id>_<title-slug>/renders/custom/<render-id>/`
- 搜尋既有輸出：`find outputs/videos -name manifest.json`

Render manifest 會記錄 source fingerprint、transcription provider、publish state，例如 `not-uploaded`。

舊版 output layouts 不會自動遷移；如果你有舊版輸出，請先確認不再需要後再手動刪除。

請不要 commit `outputs/`、`.work/`、generated media 或 `.env`。

## API 與 CLI

API 啟動後：

- API: <http://127.0.0.1:8765>
- Swagger UI: <http://127.0.0.1:8765/docs>
- Health: <http://127.0.0.1:8765/health>
- `POST /api/v1/analysis-jobs`
- `POST /api/v1/direct-render-jobs`
- Upload 相關 endpoint 目前仍會回傳 `UPLOAD_NOT_IMPLEMENTED`。

CLI help 是指令細節的主要參考：

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

如果你的 shell 文件需要 literal command substitution，同一個 volume 可以寫成：

```text
$(pwd)/outputs:/app/outputs
```

Host CLI 連 Docker API 時，設定：

```env
API_BASE_URL=http://127.0.0.1:8765
```

## 開發

```bash
uv sync --extra dev
uv run ruff check .
uv run pytest
```

如果你修改了 `cast_api` 會載入的 package code，請更新 editable install 並自行重啟 API：

```bash
uv pip install -e .
uv run cast_api
```

AI coding agents 請遵守 [AGENTS.md](AGENTS.md)。人類操作以 README 與 CLI help 為主要入口。

## 疑難排解

Server 連不上：

```bash
uv run cast_api
curl -fsS http://127.0.0.1:8765/health
```

缺少 FFmpeg：

```bash
ffmpeg -version
ffprobe -version
```

YouTube 下載失敗通常代表影片非公開、所在地區不可觀看、需要登入，或本機 downloader 需要檢查。Operation logs 在 `outputs/videos/.../logs/`。

Server 重啟後出現 `JOB_NOT_FOUND` 是預期行為：active job ID 是 process-local。請在目前 server process 重新分析，或查看 `outputs/videos/` 裡的既有 artifacts。

## License

MIT. See [LICENSE](LICENSE).
