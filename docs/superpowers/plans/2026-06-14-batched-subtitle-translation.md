# Batched Subtitle Translation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Translate long candidate subtitle lists in reliable ordered batches while preserving strict one-to-one source mapping.

**Architecture:** `LingoEngine.translate_clip` will partition selected transcript segments into sequential batches of at most 40 items. Each model response is checked against that batch's exact ordered IDs before its translations are accumulated; the existing `prepare_subtitle_items` method remains responsible for whole-clip timing, readable text, and final mapping validation.

**Tech Stack:** Python 3.13, Pydantic v2, pytest, pytest-asyncio, Ruff.

---

### Task 1: Batch Long Translation Requests

**Files:**
- Modify: `src/insightcast/engines/lingo_engine.py`
- Test: `tests/unit/test_lingo_engine.py`

- [x] **Step 1: Add a recording structured client**

Add a test client that records parsed prompt payloads and returns one configured
`TranslationResponse` per call:

```python
class RecordingTranslationClient:
    def __init__(self, responses: list[TranslationResponse]) -> None:
        self.responses = responses
        self.calls: list[dict[str, object]] = []

    async def parse(self, **kwargs: object) -> TranslationResponse:
        self.calls.append(kwargs)
        return self.responses.pop(0)
```

- [x] **Step 2: Add a failing ordered batching test**

Build 85 one-second segments and configured responses for IDs `s0` through `s84`.
Call `translate_clip`, then assert:

```python
assert len(client.calls) == 3
assert [len(json.loads(call["user_prompt"])["items"]) for call in client.calls] == [
    40,
    40,
    5,
]
assert [item.segment_id for item in result] == [f"s{index}" for index in range(85)]
```

- [x] **Step 3: Run the test and verify RED**

Run:

```bash
uv run pytest tests/unit/test_lingo_engine.py::test_translate_clip_batches_long_requests_in_source_order -q
```

Expected: FAIL because `translate_clip` currently makes one request.

- [x] **Step 4: Implement sequential batching**

Add:

```python
TRANSLATION_BATCH_SIZE = 40
```

Select the overlapping segments once, iterate over them with
`range(0, len(selected), TRANSLATION_BATCH_SIZE)`, call the client for each batch,
validate exact ordered IDs, accumulate translations, and finally call
`prepare_subtitle_items`.

- [x] **Step 5: Run the focused test and verify GREEN**

Run:

```bash
uv run pytest tests/unit/test_lingo_engine.py::test_translate_clip_batches_long_requests_in_source_order -q
```

Expected: PASS.

### Task 2: Report Invalid Later Batches

**Files:**
- Modify: `src/insightcast/engines/lingo_engine.py`
- Test: `tests/unit/test_lingo_engine.py`

- [x] **Step 1: Add a failing later-batch mismatch test**

Configure 45 source segments. Return all 40 items for batch zero and only four of
five items for batch one. Assert:

```python
assert exc_info.value.error_code == ErrorCode.SUBTITLE_GENERATION_FAILED
assert exc_info.value.details["batch_index"] == 1
assert exc_info.value.details["source_segment_ids"] == [
    "s40",
    "s41",
    "s42",
    "s43",
    "s44",
]
assert exc_info.value.details["translation_segment_ids"] == [
    "s40",
    "s41",
    "s42",
    "s43",
]
```

- [x] **Step 2: Run the test and verify RED**

Run:

```bash
uv run pytest tests/unit/test_lingo_engine.py::test_translate_clip_reports_later_batch_mapping_mismatch -q
```

Expected: FAIL because batch diagnostics do not exist.

- [x] **Step 3: Implement batch-level validation diagnostics**

Before accumulating each response, compare:

```python
source_ids = [segment.segment_id for segment in batch]
translation_ids = [item.segment_id for item in response.items]
```

Raise `SUBTITLE_GENERATION_FAILED` with `batch_index`, `source_segment_ids`, and
`translation_segment_ids` when they differ.

- [x] **Step 4: Run LingoEngine tests**

Run:

```bash
uv run pytest tests/unit/test_lingo_engine.py -q
```

Expected: PASS.

### Task 3: Repository Verification

**Files:**
- Verify: `src/insightcast/engines/lingo_engine.py`
- Verify: `tests/unit/test_lingo_engine.py`

- [x] **Step 1: Run Ruff**

Run:

```bash
uv run ruff check .
```

Expected: PASS.

- [x] **Step 2: Run the full test suite**

Run:

```bash
uv run pytest -q
```

Expected: all tests pass.

- [x] **Step 3: Check the final diff**

Run:

```bash
git diff --check
git status --short
```

Expected: only the implementation plan, LingoEngine, and its focused tests are
modified.
