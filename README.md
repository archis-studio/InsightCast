# Insight Cast

Insight Cast 是開源、本機優先的 AI 知識策展工具。它將英文 YouTube 長篇 Podcast
或對談完整轉錄，挑選具完整脈絡的中等長度片段，並產生英文與台灣繁體中文字幕影片。

MVP 以 FastAPI 與 Swagger UI 作為操作介面，不包含前端、登入、資料庫、Celery、
YouTube OAuth 或實際上傳功能。Job registry 與 queue 只存在目前 server process；
重啟後不會自動恢復 job，但 `job_state.json`、輸出檔與 log 會保留供人工檢查。

## 功能範圍

- 自動分析：下載 YouTube、完整英文轉錄、產生指定數量的候選片段。
- 候選 render：使用一個或多個 candidate ID 產生繁中 SRT、雙語 ASS 與 burned MP4。
- 直接 render：指定單一開始與結束時間，不經 Curator。
- 上傳 stub：確認 rendered video 與 metadata 存在後回傳 `UPLOAD_NOT_IMPLEMENTED`。
- 僅支援 YouTube URL 與英文來源音訊；不支援本機影片、speaker diarization 或 4K-first。

## 架構

```text
FastAPI / Swagger UI
        |
JobService + process-local registries
        |
single asyncio FIFO worker
        |
Source / Lingo / Curator / Clip / Publish Engines
        |
OpenAI / yt-dlp / FFmpeg adapters
        |
outputs/ + .work/
```

FastAPI 只負責輸入驗證與 HTTP contract。`JobService` 控制狀態與 pipeline；engines
負責應用行為；infrastructure clients 隔離 SDK 與 subprocess；prompt 以版本化模組保存；
storage 原子寫入 JSON，但不負責重建歷史 job。

## 系統需求

- Python 3.12 或更新版本（由 uv 管理時不必使用系統 Python）
- [uv](https://docs.astral.sh/uv/)
- 含 libass 支援的 FFmpeg 與 ffprobe
- 網路連線與有效的 OpenAI API key
- 足夠的磁碟空間存放來源影片、中間音訊與輸出影片

macOS：

```bash
brew install uv ffmpeg
```

Ubuntu / Debian：

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
sudo apt update
sudo apt install -y ffmpeg
```

確認工具：

```bash
uv --version
ffmpeg -version
ffprobe -version
```

## 安裝與設定

```bash
git clone <repository-url>
cd Insight-Cast
cp .env.example .env
uv sync --extra dev
```

編輯 `.env`，至少將 `OPENAI_API_KEY` 換成有效值。請勿提交 `.env`、在 issue
貼出 API key，或把 key 放入 curl request。專案啟動時會拒絕空白與常見 placeholder。

### 環境變數

| 變數 | 必要 | 預設 | 說明與範例 |
| --- | --- | --- | --- |
| `API_HOST` | 否 | `127.0.0.1` | API 綁定位置；本機使用預設值。 |
| `API_PORT` | 否 | `8765` | API port，範圍 1-65535。 |
| `OUTPUT_DIR` | 否 | `outputs` | Job state、來源與完成品根目錄。 |
| `WORK_DIR` | 否 | `.work` | 可診斷的暫存 clip 與 audio chunks。 |
| `OPENAI_API_KEY` | 是 | 無 | OpenAI API key，例如 `sk-...`。 |
| `OPENAI_BASE_URL` | 否 | 空白 | OpenAI-compatible endpoint；官方 API 留空。 |
| `LLM_MODEL` | 否 | `gpt-5.4-mini` | 文字 AI 的 fallback model。 |
| `CURATOR_MODEL` | 否 | 空白 | Curator model；空白回退 `LLM_MODEL`。 |
| `TRANSLATION_MODEL` | 否 | 空白 | 翻譯 model；空白回退 `LLM_MODEL`。 |
| `METADATA_MODEL` | 否 | 空白 | Metadata model；空白回退 `LLM_MODEL`。 |
| `TRANSCRIPTION_PROVIDER` | 否 | `openai` | `openai` 或 `local`。 |
| `OPENAI_TRANSCRIPTION_MODEL` | 否 | `whisper-1` | 需要 timestamped segments 的轉錄 model。 |
| `OPENAI_TRANSCRIPTION_MAX_UPLOAD_MB` | 否 | `24` | 單一 audio chunk 上限，最大 25 MB。 |
| `WHISPER_MODEL_SIZE` | 否 | `large-v3` | faster-whisper model size。 |
| `WHISPER_DEVICE` | 否 | `auto` | faster-whisper device，例如 `cpu`、`cuda`。 |
| `FFMPEG_BIN` | 否 | `ffmpeg` | FFmpeg executable path。 |
| `VIDEO_MAX_HEIGHT` | 否 | `1080` | yt-dlp 下載解析度上限。 |
| `VIDEO_CRF` | 否 | `18` | H.264 CRF，數值越低通常品質與檔案越大。 |
| `OPENAI_TIMEOUT_SECONDS` | 否 | `120` | OpenAI request timeout。 |
| `OPENAI_MAX_RETRIES` | 否 | `2` | Structured request retry 次數。 |

### 本機 Whisper fallback

```bash
uv sync --extra dev --extra local-whisper
```

```env
TRANSCRIPTION_PROVIDER=local
WHISPER_MODEL_SIZE=large-v3
WHISPER_DEVICE=auto
```

Model 第一次使用時才下載與載入。`large-v3` 需要數 GB 磁碟與大量記憶體，CPU
轉錄通常明顯慢於影片播放時間；資源不足時可改用較小 model。Curator、翻譯與 metadata
仍需 OpenAI API，因此 local transcription 不代表可移除 `OPENAI_API_KEY`。

## 測試與啟動

```bash
uv sync --extra dev
uv run ruff check .
uv run pytest
uv run cast_api
```

預設位置：

- API：<http://127.0.0.1:8765>
- Swagger UI：<http://127.0.0.1:8765/docs>
- OpenAPI JSON：<http://127.0.0.1:8765/openapi.json>

啟動時會驗證設定與 FFmpeg。Local Whisper model 不會在 startup 下載。

## Swagger / API 操作

### 1. 建立分析 job

`POST /api/v1/analysis-jobs`

```bash
curl -sS -X POST http://127.0.0.1:8765/api/v1/analysis-jobs \
  -H 'Content-Type: application/json' \
  -d '{
    "youtube_url": "https://www.youtube.com/watch?v=abc123DEF_-",
    "candidate_count": 2,
    "min_duration_minutes": 8,
    "max_duration_minutes": 12,
    "force_reanalyze": false
  }'
```

同一 server process 內，相同 normalized URL 會重用最新 analysis job。
`force_reanalyze=true` 會建立新 job。分析成功後停在 `WAITING_SELECTION`。

```bash
curl -sS http://127.0.0.1:8765/api/v1/analysis-jobs/ANALYSIS_JOB_ID
```

### 2. Render 候選

```bash
curl -sS -X POST \
  http://127.0.0.1:8765/api/v1/analysis-jobs/ANALYSIS_JOB_ID/renders \
  -H 'Content-Type: application/json' \
  -d '{"candidate_ids":["A","B"],"force_render":false}'
```

`candidate_ids` 也可使用字串 `"A"`。已完成的 candidate 預設跳過並回傳現有 artifacts；
`force_render=true` 會建立新的 timestamped batch。

```bash
curl -sS \
  http://127.0.0.1:8765/api/v1/analysis-jobs/ANALYSIS_JOB_ID/renders
```

### 3. 直接 Render

`POST /api/v1/direct-render-jobs`

```bash
curl -sS -X POST http://127.0.0.1:8765/api/v1/direct-render-jobs \
  -H 'Content-Type: application/json' \
  -d '{
    "youtube_url": "https://www.youtube.com/watch?v=abc123DEF_-",
    "start_time": "00:12:30",
    "end_time": "00:22:00"
  }'
```

```bash
curl -sS http://127.0.0.1:8765/api/v1/direct-render-jobs/DIRECT_JOB_ID
```

時間可使用 `HH:MM:SS(.mmm)` 或 numeric seconds；一次 request 只接受一段範圍。

### 4. YouTube upload stub

```bash
curl -sS -X POST \
  http://127.0.0.1:8765/api/v1/analysis-jobs/ANALYSIS_JOB_ID/youtube-uploads

curl -sS -X POST \
  http://127.0.0.1:8765/api/v1/direct-render-jobs/DIRECT_JOB_ID/youtube-uploads
```

Endpoint 會先確認 burned MP4 與 metadata JSON 存在，再以 HTTP 501 回傳
`UPLOAD_NOT_IMPLEMENTED` 和可發布檔案的絕對路徑。

## 輸出與檔案生命週期

```text
outputs/
  <timestamp>_<video-title>_<job-id>/
    job_state.json
    pipeline.log
    source/
      <title>.source.mp4
      <title>.audio.mp3
    analysis/
      transcript.json
      candidates.json
    renders/
      <timestamp>/
        candidate-a/
          <title>.a.zh-TW.srt
          <title>.a.bilingual.ass
          <title>.a.bilingual.burned.mp4
          <title>.a.youtube-metadata.json
```

Direct render 使用單獨的 `<timestamp>_<title>_direct_<job-id>/render/`。成功 render
會刪除 `.work/` 下的 unburned clip；失敗會保留暫存檔供診斷。來源影片保留，以便之後
render 其他 candidate。API 回傳 artifact 的絕對本機路徑。

## 效能預期

所有 CPU-intensive pipeline work 共用一個 FIFO worker，因此不會同時執行兩個
download/transcription/render job。1080p H.264 re-encode 與本機 Whisper 可能需要
數十分鐘到數小時，取決於影片長度、CPU/GPU、網路與 model。處理前請預留來源影片
數倍的暫存與輸出空間。

## 疑難排解

### Startup 拒絕 API key

確認 `.env` 存在，`OPENAI_API_KEY` 不是空白、`replace-me` 或其他 placeholder。
不要在 shell history、log 或公開訊息貼出 key。

### `FFMPEG_NOT_AVAILABLE`

執行 `ffmpeg -version`，確認 executable 在 `PATH`。自訂位置可設定
`FFMPEG_BIN=/absolute/path/to/ffmpeg`。字幕燒錄另需 FFmpeg build 支援 libass。

### `YOUTUBE_DOWNLOAD_FAILED`

先更新 lockfile 所指定的 yt-dlp 套件、確認 URL 可公開存取與網路正常。私人、區域限制、
DRM 或需要登入的影片不保證可下載；詳細 stderr 位於 job 的 `pipeline.log`。

### `UNSUPPORTED_LANGUAGE`

MVP 僅接受英文來源音訊。自動偵測為其他語言時 job 會失敗。

### OpenAI / model 錯誤

檢查 API key、account quota、`OPENAI_BASE_URL`、model 名稱與網路。Structured output
失敗會自動重試；完整 traceback 僅寫入 `pipeline.log`，API 只回安全 details。

### 本機 Whisper 下載或記憶體不足

確認網路與模型 cache 空間，或改用較小 `WHISPER_MODEL_SIZE`。CPU 環境可明確設定
`WHISPER_DEVICE=cpu`。

### Server 重啟後找不到 job

這是 MVP 限制。Registry 不會從磁碟 restore；請直接查看 `outputs/` 中的
`job_state.json`、artifacts 與 `pipeline.log`。

## 著作權與合法使用

Insight Cast 不授予下載、編輯、重製、公開傳輸或再發布第三方內容的權利。使用者必須
自行取得必要授權、遵守 YouTube 條款與所在地法律，並對所有輸入與輸出內容負責。

