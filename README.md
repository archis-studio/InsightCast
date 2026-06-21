# Insight Cast

Insight Cast 是開源、本機優先的 AI 知識策展工具。它將英文 YouTube 長篇 Podcast
或對談完整轉錄，挑選具完整脈絡的中等長度片段，並產生英文與台灣繁體中文字幕影片。

MVP 以 FastAPI 與 Swagger UI 作為操作介面，不包含前端、登入、資料庫、Celery、
YouTube OAuth 或實際上傳功能。Job registry 與 queue 只存在目前 server process；
重啟後不會自動恢復 job，但 video、analysis、render manifests、輸出檔與 operation
logs 會保留，並可由 video ID 重新查找。

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

- Python 3.13 或更新版本（由 uv 管理時不必使用系統 Python）
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

## Quick Start：從 clone 到第一支剪輯

這是新使用者或 AI agent 最短、最穩定的操作路徑。所有命令都從 repository root 執行。

1. 安裝 dependency 並確認工具：

   ```bash
   uv sync --extra dev
   uv --version
   ffmpeg -version
   ffprobe -version
   ```

2. 建立並編輯本機設定：

   ```bash
   cp .env.example .env
   ```

   至少設定有效的 `OPENAI_API_KEY`。如果要讓 agent 操作，請由使用者自己準備 `.env`；
   agent 不應要求貼出 key，也不應把 `.env` 內容輸出到對話或 log。

3. 在第一個 terminal 啟動 API server：

   ```bash
   uv run cast_api
   ```

   保持這個 process 持續執行。Analysis job ID 與 render queue 都存在目前 server
   process；server 重啟後，舊 job ID 不會恢復，但 `outputs/videos` 裡的 manifests
   與完成品仍會保留。

4. 在第二個 terminal 確認 server 存活並分析 YouTube：

   ```bash
   curl -fsS http://127.0.0.1:8765/health
   uv run cast_analyze "https://www.youtube.com/watch?v=abc123DEF_-"
   ```

   `WAITING_SELECTION` 代表分析成功完成。選一個 candidate ID，例如 `B`。

5. 只有在要產生剪輯影片時才 render：

   ```bash
   uv run cast_render ANALYSIS_JOB_ID B --wait
   ```

   完成後，CLI 會列出 render directory、`video.mp4`、繁中 SRT、雙語 ASS、
   YouTube metadata、manifest、`stage-manifest.json` 與 operation log。若 `ffprobe`
   可用，也會驗證 MP4 duration 與 size。

給 AI agent 的操作原則：不要自行啟動或停止 `cast_api`，除非使用者明確要求；先確認
server 存活，再跑 `cast_analyze`；只有使用者明確指定 candidate 時才跑 `cast_render`。

### 環境變數

| 變數 | 必要 | 預設 | 說明與範例 |
| --- | --- | --- | --- |
| `API_HOST` | 否 | `127.0.0.1` | API 綁定位置；本機使用預設值。 |
| `API_PORT` | 否 | `8765` | API port，範圍 1-65535。 |
| `API_BASE_URL` | 否 | `http://127.0.0.1:8765` | CLI 使用的 client-facing API URL，可與 server binding 不同。 |
| `ANALYZE_POLL_INTERVAL_SECONDS` | 否 | `30` | Analysis CLI 狀態輪詢秒數，必須大於 0。 |
| `OUTPUT_DIR` | 否 | `outputs` | Job state、來源與完成品根目錄。 |
| `WORK_DIR` | 否 | `.work` | 可診斷的暫存 clip 與 audio chunks。 |
| `DEFAULT_CANDIDATE_COUNT` | 否 | `2` | Analysis request 未提供 override 時的候選數量，範圍 1-26。 |
| `DEFAULT_MIN_DURATION_MINUTES` | 否 | `8` | 候選片段預設最短分鐘數，必須大於 0。 |
| `DEFAULT_MAX_DURATION_MINUTES` | 否 | `12` | 候選片段預設最長分鐘數，必須不小於最短值。 |
| `OPENAI_API_KEY` | 是 | 無 | OpenAI API key，例如 `sk-...`。 |
| `OPENAI_BASE_URL` | 否 | 空白 | OpenAI-compatible endpoint；官方 API 留空。 |
| `LLM_MODEL` | 否 | `gpt-5.4-mini` | 文字 AI 的 fallback model。 |
| `CURATOR_MODEL` | 否 | 空白 | Curator model；空白回退 `LLM_MODEL`。 |
| `TRANSLATION_MODEL` | 否 | 空白 | 翻譯 model；空白回退 `LLM_MODEL`。 |
| `METADATA_MODEL` | 否 | 空白 | Metadata model；空白回退 `LLM_MODEL`。 |
| `TRANSCRIPTION_PROVIDER` | 否 | `openai` | `openai` 或 `local`。 |
| `OPENAI_TRANSCRIPTION_MODEL` | 否 | `whisper-1` | 需要 timestamped segments 的轉錄 model。 |
| `OPENAI_TRANSCRIPTION_MAX_UPLOAD_MB` | 否 | `8` | 單一 audio chunk 上限，最大 25 MB；長片建議維持較小以降低轉錄 timeout。 |
| `OPENAI_TRANSCRIPTION_MAX_ATTEMPTS` | 否 | `3` | 單一 audio chunk 轉錄失敗時的最大嘗試次數，範圍 1-10。 |
| `OPENAI_TRANSCRIPTION_RETRY_SLEEP_SECONDS` | 否 | `0` | OpenAI 轉錄 chunk retry 間隔秒數；可在 rate limit 或暫時性錯誤時調高。 |
| `WHISPER_MODEL_SIZE` | 否 | `large-v3` | faster-whisper model size。 |
| `WHISPER_DEVICE` | 否 | `auto` | faster-whisper device，例如 `cpu`、`cuda`。 |
| `FFMPEG_BIN` | 否 | `ffmpeg` | FFmpeg executable path。 |
| `YTDLP_JS_RUNTIME` | 否 | `node` | 傳給 yt-dlp 的 JavaScript runtime，例如 `node`、`deno` 或 `bun`；設為空白可停用。 |
| `VIDEO_MAX_HEIGHT` | 否 | `1080` | yt-dlp 下載解析度上限。 |
| `VIDEO_CRF` | 否 | `18` | H.264 CRF，數值越低通常品質與檔案越大。 |
| `SUBTITLE_CHINESE_FONT_SIZE` | 否 | `72` | 燒錄雙語 ASS 時繁中文字級；手機觀看可調大。 |
| `SUBTITLE_ENGLISH_FONT_SIZE` | 否 | `60` | 燒錄雙語 ASS 時英文字級；通常略小於繁中。 |
| `OPENAI_TIMEOUT_SECONDS` | 否 | `120` | OpenAI request timeout。 |
| `OPENAI_MAX_RETRIES` | 否 | `2` | Structured request retry 次數。 |
| `OPENAI_RETRY_SLEEP_SECONDS` | 否 | `10` | Structured OpenAI request retry 間隔秒數。 |

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

### `cast_api` 參數與設定

`cast_api` 是 API server entrypoint，不接受 CLI options；設定全部來自 `.env` 或環境變數。

```bash
uv run cast_api
```

常用設定：

| 設定 | 作用 |
| --- | --- |
| `API_HOST` | Server bind host。Docker 內通常設為 `0.0.0.0`；本機預設 `127.0.0.1`。 |
| `API_PORT` | Server bind port，預設 `8765`。 |
| `OUTPUT_DIR` | 持久化 video、analysis、render、log 的根目錄。 |
| `WORK_DIR` | pipeline 暫存目錄；失敗時可能保留診斷檔。 |
| `OPENAI_API_KEY` | 必要。不能是空白或 placeholder。 |
| `FFMPEG_BIN` | FFmpeg executable path。 |

`API_BASE_URL` 不影響 server binding；它只控制 CLI 要連到哪個 API URL。若 server
無法啟動，先看 terminal 中的 settings validation、FFmpeg 檢查或 port already in use。

## Analysis CLI

API server lifecycle 與分析命令分開管理。先在一個 terminal 啟動 server：

```bash
uv run cast_api
```

再從 repository root 的另一個 terminal 執行：

```bash
uv run cast_analyze "https://www.youtube.com/watch?v=abc123DEF_-"
```

命令會先檢查 `/health`，建立一個 analysis job，立即開始輪詢，並在每次 poll
顯示 status、API message 與總經過時間。`WAITING_SELECTION` 代表分析成功完成；
此時命令會列出 candidate ID、標題、時間範圍、摘要，以及 video root、analysis ID
與目錄、transcript ID 與路徑、candidate 目錄、預期 render 目錄和 operation log。
這個命令只分析，不會 render candidate。

需要完整 API payload 診斷時使用：

```bash
uv run cast_analyze --verbose "https://www.youtube.com/watch?v=abc123DEF_-"
```

可用參數：

| 參數 | 說明 |
| --- | --- |
| `youtube_url` | 必填。YouTube watch、share、embed 或 Shorts URL。 |
| `--verbose` | 每次成功 API request 後印出完整 JSON response，適合診斷 API payload。 |
| `--force` | 不重用同一 server process 內相同 URL 的最新 analysis job，強制建立新 analysis。 |
| `-h`, `--help` | 顯示 CLI help。 |

CLI 不會啟動、停止或重啟 server，也沒有整體 timeout。`Ctrl-C` 只停止本機監看，
server job 可能仍會繼續。Exit code `0` 表示到達 `WAITING_SELECTION`，`1` 表示 API
或 analysis failure，`2` 表示參數或本機設定無效，`130` 表示使用者中斷。
`API_BASE_URL` 是 CLI 連線位置；`API_HOST` 與 `API_PORT` 仍只控制 server binding。

### AI / operator quick path

收到「分析 YouTube URL」時，先確認 server 是使用者另外啟動的：

```bash
curl -fsS http://127.0.0.1:8765/health
ps -axo pid,command | rg 'uv run cast_api|cast_api'
```

健康檢查通過後，從 repository root 執行：

```bash
uv run cast_analyze "YOUTUBE_URL"
```

`WAITING_SELECTION` 就是分析成功。回報時列出 candidate ID、標題、時間範圍、摘要、
video root、analysis ID 與目錄、transcript ID 與路徑、candidate 目錄、operation
log，以及 log 中是否有 `source_cache_hit`、`transcript_cache_hit` 或 cache miss。
只有使用者明確要求 render 時才進入下一節。Render candidate 時優先使用 CLI：

```bash
uv run cast_render ANALYSIS_JOB_ID B --wait
```

`cast_render` 只呼叫既有 API，不會直接操作 pipeline internals；`--wait` 會輪詢 render
list、顯示目前 stage，完成後列出 video、SRT、ASS、metadata、manifest 與
`stage-manifest.json`，並在 `ffprobe` 可用時驗證 MP4 duration 與 size。

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
`candidate_count`、`min_duration_minutes`、`max_duration_minutes` 都是可省略的
server-default override；可只提供其中一個欄位。要使用預設值時必須省略欄位，
明確傳入 `null` 會回傳 HTTP 422。

```bash
curl -sS http://127.0.0.1:8765/api/v1/analysis-jobs/ANALYSIS_JOB_ID
```

### 2. Render 候選

CLI quick path：

```bash
uv run cast_render ANALYSIS_JOB_ID A B --wait
```

預設會重用 ready renders；需要強制建立新 render batch 時加：

```bash
uv run cast_render ANALYSIS_JOB_ID B --wait --force-render
```

可用參數：

| 參數 | 說明 |
| --- | --- |
| `job_id` | 必填。`cast_analyze` 回報的 analysis job ID；只在目前 running server process 內有效。 |
| `candidate_ids` | 必填，可一個或多個，例如 `B` 或 `A B`；CLI 會轉成大寫。 |
| `--wait` | 輪詢 render list，直到 batch `COMPLETED` 或 `FAILED`。一般操作建議加上。 |
| `--force-render` | 即使已有可重用 artifacts 也建立新 render；只有明確需要新版本時使用。 |
| `-h`, `--help` | 顯示 CLI help。 |

CLI 會先檢查 `/health`，再呼叫下列 API。低階診斷或自動化整合可直接使用 API：

```bash
curl -sS -X POST \
  http://127.0.0.1:8765/api/v1/analysis-jobs/ANALYSIS_JOB_ID/renders \
  -H 'Content-Type: application/json' \
  -d '{"candidate_ids":["A","B"],"force_render":false}'
```

`candidate_ids` 也可使用字串 `"A"`。已完成的 candidate 預設跳過並回傳現有 artifacts；
`force_render=true` 會建立新的 timestamped batch。預設情況下，系統會重用 ready
renders 並從安全 checkpoint resume。

Render responses 會包含 stage summaries。`cast_render --wait` 會把目前 running stage
直接印出；需要詳細 resume 與錯誤診斷時，查看 render 目錄中的
`stage-manifest.json`。

```bash
curl -sS \
  http://127.0.0.1:8765/api/v1/analysis-jobs/ANALYSIS_JOB_ID/renders
```

AI 或 operator render 單一候選時，使用上一輪 analysis job ID，只送使用者指定的
candidate，例如：

```bash
uv run cast_render ANALYSIS_JOB_ID B --wait
```

`cast_render --wait` 會輪詢 render list 到 batch `status` 變成 `COMPLETED` 或
`FAILED`。成功時應確認：

- `candidate_results.<ID>.error` 是 `null`。
- `manifest.json` 的 `render_state` 是 `ready`，`publish_state` 是 `not-uploaded`。
- `stage-manifest.json` 中 `cut_clip`、`translate_subtitles`、`write_subtitles`、
  `burn_subtitles`、`generate_metadata`、`validate_render` 都是 `completed`。
- `video.mp4`、`subtitles.zh-TW.srt`、`subtitles.bilingual.ass`、
  `youtube-metadata.json` 都存在且非空。
- 可用 `ffprobe -v error -show_entries format=duration,size -of json path/to/video.mp4`
  驗證 MP4 可解析，並回報 duration 與 size。

回報 render 結果時列出 render ID、output directory、manifest、stage manifest、
burned video、SRT、ASS、metadata 與 operation log。失敗時先看 API response 的
structured error，再看 `stage-manifest.json` 與 `logs/<operation-id>.log`。

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

### 4. 查詢持久化結果

Server 重啟後 process-local job ID 不會恢復，但 video manifest、analysis 與 render
可由磁碟重新發現：

```bash
curl -sS http://127.0.0.1:8765/api/v1/videos/VIDEO_ID
curl -sS http://127.0.0.1:8765/api/v1/videos/VIDEO_ID/analyses
curl -sS http://127.0.0.1:8765/api/v1/videos/VIDEO_ID/renders
```

### 5. YouTube upload stub

上傳必須指定一個已完成且可發布的 render ID，不能只指定 analysis 或 direct-render
job：

```bash
curl -sS -X POST \
  http://127.0.0.1:8765/api/v1/videos/VIDEO_ID/renders/RENDER_ID/youtube-uploads
```

Endpoint 會先確認 burned MP4 與 metadata JSON 存在，再以 HTTP 501 回傳
`UPLOAD_NOT_IMPLEMENTED` 和可發布檔案的絕對路徑。

## 輸出與檔案生命週期

所有新資料都使用 video-centric layout。`<video-id>_<title-slug>` 在第一次看到影片時
建立；後續即使 URL form 或 YouTube 標題改變，仍以 `video.json` 的 video ID 找回同一
個 video root。

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
          manifest.json
          transcript.json
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

完整 candidate render 路徑模板是
`analyses/<analysis-id>/candidates/A/renders/<render-id>/`；直接 render 則位於
`renders/custom/<render-id>/`。檔名固定，因此不要用影片標題猜測完成品名稱。

| 資料 | 相對於 video root 的位置 |
| --- | --- |
| Video manifest | `video.json` |
| Source manifest / video / audio | `source/manifest.json`、`source/source.mp4`、`source/audio.mp3` |
| Transcript | `transcripts/<transcript-id>/manifest.json` 與 `transcript.json` |
| Analysis | `analyses/<analysis-id>/manifest.json` 與 `candidates.json` |
| Candidate | `analyses/<analysis-id>/candidates/<candidate-id>/candidate.json` |
| Candidate render | `analyses/<analysis-id>/candidates/<candidate-id>/renders/<render-id>/` |
| Direct render | `renders/custom/<render-id>/` |
| Render artifacts | `video.mp4`、`subtitles.zh-TW.srt`、`subtitles.bilingual.ass`、`youtube-metadata.json` |
| Operation log | `logs/<operation-id>.log` |

### 查找 video、analysis 與 render

先用 `video.json` 找 video root，再從 immutable analysis 選擇 candidate `A/B/C`，
最後查看該 candidate 下的 render versions：

```bash
find outputs/videos -name video.json -print
find "outputs/videos/${VIDEO_ID}_"* -path "*/analyses/*/manifest.json" -print
find "outputs/videos/${VIDEO_ID}_"* -path "*/candidates/A/renders/*/video.mp4" -print
find "outputs/videos/${VIDEO_ID}_"* -path "*/renders/custom/*/manifest.json" -print
jq '{render_id, render_state, publish_state, artifacts}' path/to/manifest.json
```

Analysis manifest 記錄 analysis ID、transcript ID、Curator model、prompt version、
requested duration、candidate paths 與 operation log。每次 forced analysis 建立新的
`<analysis-id>`，不覆寫先前 analysis。Render manifest 以 `render_state` 記錄
`queued`、`rendering`、`ready`、`failed`，以 `publish_state` 記錄
`not-uploaded`、`uploading`、`uploaded`、`upload-failed`；實際上傳在 MVP 尚未實作。

### Source 與 transcript reuse

`source/manifest.json` 保存 source fingerprint（source MP4 的 SHA-256）、固定相對路徑、
檔案大小及下載 metadata。每次 reuse 都會驗證 manifest、一般檔案、非空大小與
SHA-256；完整且一致才重用。缺少 source 是 cache miss，損壞、路徑不符或 hash 不符
是 repair，下一次分析會以 staging directory 重建並原子替換 `source/`。

Transcript cache key 由 source fingerprint、transcription provider、transcription
model、language 與 transcript schema version 共同計算。相同 key 會重用既有
`transcripts/<transcript-id>/`；任一輸入改變都會建立不同 transcript。CLI 會顯示
transcript ID 與路徑，operation log 會以 `transcript_cache_hit` 或
`transcript_cache_miss` 記錄 reuse 結果。

不同 watch、share、embed 或 Shorts URL 會解析成同一個 YouTube video ID，因此不會
建立不同 cache。可用以下命令檢查 source readiness、大小及 fingerprint，或只清除
source：

```bash
uv run cast_cache list
uv run cast_cache remove abc123DEF_-
uv run cast_cache clear --yes
```

`remove` 與 `clear --yes` 只刪除 managed video root 下的 `source/`，保留
`video.json`、transcripts、analyses、renders 與 logs。之後需要 source 的工作會重新
下載；既有完成品仍可查閱。

### Restart 與舊版目錄

Process-local job registry 與 FIFO queue 在 server 重啟後不會恢復，但
`GET /api/v1/videos/{video_id}/analyses` 與
`GET /api/v1/videos/{video_id}/renders` 會從 manifest 掃描持久化結果。損壞、
不完整或路徑不安全的 manifest 不會被當成可發布 render。

舊版 `outputs/jobs/` 與 `outputs/source-cache/` 不屬於 managed video store，會被新版
程式忽略，也不會自動遷移。確認不再需要舊版 artifacts 後可手動移除；`cast_cache`
不會刪除這些目錄。

成功 render 會刪除 `.work/` 下的 unburned clip；失敗會保留暫存檔供診斷。API 回傳
artifact 的絕對本機路徑。

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
DRM 或需要登入的影片不保證可下載。video root 建立後，詳細 stderr 與 traceback 位於
`logs/<operation-id>.log`；若失敗發生在 video root 建立前，才會暫存在該 job 的
`pipeline.log`。

### `UNSUPPORTED_LANGUAGE`

MVP 僅接受英文來源音訊。自動偵測為其他語言時 job 會失敗。

### OpenAI / model 錯誤

檢查 API key、account quota、`OPENAI_BASE_URL`、model 名稱與網路。Structured output
失敗會自動重試；完整 traceback 寫入 operation log，API 只回安全 details。

### 本機 Whisper 下載或記憶體不足

確認網路與模型 cache 空間，或改用較小 `WHISPER_MODEL_SIZE`。CPU 環境可明確設定
`WHISPER_DEVICE=cpu`。

### Server 重啟後找不到 job

這是 MVP 的 process-local registry 限制。Analysis/direct-render job endpoint 不會
restore 舊 job ID；請改用 video ID 呼叫 `/api/v1/videos/{video_id}/analyses` 或
`/api/v1/videos/{video_id}/renders`，或依上方 `find outputs/videos` 範例直接查看
manifest、artifacts 與 operation log。

## Docker

Docker image 使用 Python 3.13 CPU base、安裝 FFmpeg，並以 non-root `app` 使用者執行。
先完成 `.env` 設定，再 build：

```bash
docker build -t insightcast .
```

啟動時傳入 `.env`、映射 API port，並將 `outputs/` 掛載到 host，避免 container
刪除後遺失完成品：

```bash
docker run --rm --env-file .env -p 8765:8765 \
  -v "$(pwd)/outputs:/app/outputs" insightcast
```

啟動後開啟 <http://127.0.0.1:8765/docs>。Apple Silicon 與一般 x86_64 環境都使用
CPU image；本機 Whisper 的大型 model 與 GPU runtime 未包含在 MVP image。

## 著作權與合法使用

Insight Cast 不授予下載、編輯、重製、公開傳輸或再發布第三方內容的權利。使用者必須
自行取得必要授權、遵守 YouTube 條款與所在地法律，並對所有輸入與輸出內容負責。
