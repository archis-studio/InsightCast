# Insight Cast

Insight Cast 是一個 local-first 的 AI 影片精華剪輯工具，主要用來處理英文 YouTube 長影片：

1. 下載影片與音訊
2. 轉錄英文語音
3. 分析內容，找出適合獨立觀看的 8-12 分鐘精華片段
4. 產出含繁體中文字幕與雙語字幕的可發布影片

它適合創作者、剪輯工作者、研究者，或想要在本機保留完整 pipeline、輸出、manifest、log 的使用者。

請在下載、剪輯或發布任何來源影片前，確認你有權使用該內容，並遵守 YouTube 條款與著作權規範。

## 目前能做什麼

- 分析英文 YouTube 長影片
- 使用 OpenAI Whisper 或本機 faster-whisper 轉錄
- 選出有完整上下文的候選片段，並提供分數、理由、摘要
- 剪出候選片段並燒錄繁體中文 / 雙語字幕
- 產出 YouTube metadata 草稿
- 將影片、字幕、manifest、stage-manifest、operation log 保存在 `outputs/`
- 透過 CLI 操作主要流程，FastAPI 作為本機服務

輸出包含：

- `video.mp4`
- `subtitles.zh-TW.srt`
- `subtitles.bilingual.ass`
- `youtube-metadata.json`
- `manifest.json`
- `stage-manifest.json`

## 目前不是什麼

- 不是雲端 SaaS
- 不是多人後台
- 不是完整影片剪輯軟體
- 還不是完整 YouTube uploader
- 目前主要設計給英文來源音訊使用

API server 的 active job ID 只存在目前 process。若重啟 server，舊 job ID 不會恢復，但已產生的檔案仍會保留在 `outputs/`。

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

選一個候選片段進行 render：

```bash
uv run cast_render ANALYSIS_JOB_ID B --wait
```

CLI 會在完成後列出 render directory、影片、字幕、metadata、manifest 與 log path。

## 常見操作流程

1. 保持 `uv run cast_api` 在背景執行。
2. 使用 CLI 分析影片：

   ```bash
   uv run cast_analyze "YOUTUBE_URL"
   ```

3. 比較候選片段的分數、摘要與時間範圍。
4. 只 render 你要的候選：

   ```bash
   uv run cast_render ANALYSIS_JOB_ID A --wait
   ```

5. 檢查輸出：
   - `video.mp4`
   - `subtitles.zh-TW.srt`
   - `subtitles.bilingual.ass`
   - `youtube-metadata.json`
   - `stage-manifest.json`

強制重新分析：

```bash
uv run cast_analyze --force "YOUTUBE_URL"
```

強制重新 render：

```bash
uv run cast_render ANALYSIS_JOB_ID B --wait --force-render
```

## 設定

主要設定都在 `.env`。常用項目：

| 變數 | 預設值 | 說明 |
| --- | --- | --- |
| `OPENAI_API_KEY` | required | OpenAI API key。不要 commit。 |
| `API_HOST` | `127.0.0.1` | API bind host。Docker 可用 `0.0.0.0`。 |
| `API_PORT` | `8765` | API port。 |
| `API_BASE_URL` | `http://127.0.0.1:8765` | CLI 連線的 API URL。 |
| `OUTPUT_DIR` | `outputs` | 永久輸出：影片、字幕、transcript、manifest、log。 |
| `WORK_DIR` | `.work` | pipeline 暫存目錄。 |
| `LLM_MODEL` | `gpt-5.4-mini` | 分析、翻譯、metadata 預設文字模型。 |
| `LLM_CAPABILITY_PROFILE` | `openai_strict` | 文字模型能力 profile。`local_conservative` 會採用較小字幕翻譯批次，較適合本地或 OpenAI-compatible 模型。 |
| `TRANSLATION_BATCH_SIZE` | profile 決定 | 覆寫字幕翻譯批次大小；未設定時 `openai_strict=24`、`local_conservative=12`。 |
| `TRANSCRIPTION_PROVIDER` | `openai` | `openai` 或 `local`。 |
| `OPENAI_TRANSCRIPTION_MODEL` | `whisper-1` | OpenAI transcription model。 |
| `OPENAI_TRANSCRIPTION_TIMEOUT_SECONDS` | `240` | 單個 OpenAI transcription chunk 的 timeout；逾時會依重試設定重跑該 chunk。 |
| `VIDEO_MAX_HEIGHT` | `1080` | 下載影片高度上限。 |
| `VIDEO_CRF` | `18` | Render 品質。數字越低通常畫質越好、檔案越大。 |
| `VIDEO_X264_PRESET` | `veryfast` | x264 編碼速度設定；可改 `medium`/`slow` 換取較小檔案，畫質主要仍由 `VIDEO_CRF` 控制。 |

完整設定請看 [.env.example](.env.example)。

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

本機轉錄可能較慢，也需要下載模型。內容分析、翻譯與 metadata 仍會使用 OpenAI-compatible 文字模型。

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
          <render-id>/
      logs/
```

常用查找路徑：

- Candidate render: `outputs/videos/<video-id>_<title-slug>/analyses/<analysis-id>/candidates/A/renders/<render-id>/`
- Direct render: `outputs/videos/<video-id>_<title-slug>/renders/custom/<render-id>/`
- 搜尋既有輸出：`find outputs/videos -name manifest.json`

Render manifest 會記錄 source fingerprint、transcription provider、publish state，例如 `not-uploaded`。

舊版 output layouts 不會自動遷移；如果你有舊版輸出，請先確認不再需要後再手動刪除。這裡保留舊版提醒，是為了避免誤刪資料。

請不要 commit `outputs/`、`.work/`、`.env`。

## API 與文件

API 啟動後：

- API: <http://127.0.0.1:8765>
- Swagger UI: <http://127.0.0.1:8765/docs>
- Health: <http://127.0.0.1:8765/health>

主要建議使用 CLI：

```bash
uv run cast_analyze --help
uv run cast_render --help
uv run cast_cache --help
```

核心 API endpoints：

- `POST /api/v1/analysis-jobs`
- `POST /api/v1/direct-render-jobs`
- upload 相關 endpoint 目前仍會回傳 `UPLOAD_NOT_IMPLEMENTED`

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

如果你修改了 `cast_api` 會載入的 package code，請更新 editable install 並重啟 API：

```bash
uv pip install -e .
uv run cast_api
```

如果有改 dependency 或 `pyproject.toml`，使用：

```bash
uv sync --extra dev
```

## 給 AI coding agent 的操作規則

這個 repo 是 local-first。API server 是使用者管理的 process state。

分析或 render 前，先確認 server 正在跑：

```bash
curl -fsS http://127.0.0.1:8765/health
ps -axo pid,command | rg 'uv run cast_api|cast_api'
```

規則：

- 不要印出 `.env`、`OPENAI_API_KEY` 或含 secret 的 raw request headers。
- 除非使用者明確要求，不要啟動、停止或重啟 `uv run cast_api`。
- 分析影片使用 `uv run cast_analyze "YOUTUBE_URL"`。
- `WAITING_SELECTION` 代表分析成功完成。
- 只有使用者要求 render 時才執行：

  ```bash
  uv run cast_render ANALYSIS_JOB_ID B --wait
  ```

- render `COMPLETED` 且 `manifest.json` 的 `render_state=ready` 才算成功。
- 回報 candidate IDs、分數、時間範圍、artifact paths、operation log。
- 不要 commit generated media、`.work/`、`outputs/`、`.env`。

更完整的 agent 操作規則請看 [AGENTS.md](AGENTS.md)。

## 疑難排解

### Server 連不上

在另一個 terminal 啟動：

```bash
uv run cast_api
```

確認 health：

```bash
curl -fsS http://127.0.0.1:8765/health
```

### `OPENAI_API_KEY` 錯誤

從 `.env.example` 建立 `.env`，填入有效 key。不要把 key 貼到 issue、prompt 或 log。

### `FFMPEG_NOT_AVAILABLE`

安裝 FFmpeg 並確認：

```bash
ffmpeg -version
ffprobe -version
```

### `YOUTUBE_DOWNLOAD_FAILED`

確認影片是公開、所在地區可觀看，且不需要登入。Operation logs 通常在：

```text
outputs/videos/.../logs/
```

### `UNSUPPORTED_LANGUAGE`

目前主要支援英文來源音訊。

### Server 重啟後 `JOB_NOT_FOUND`

Job ID 是 process-local。請在目前 server process 重新分析，或直接查看 `outputs/videos/` 裡已保存的輸出。

## License

MIT. See [LICENSE](LICENSE).
