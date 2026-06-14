# Adaptive Subtitle Translation Retries Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Recover from structured translation responses that omit items by recursively splitting only the mismatched batch.

**Architecture:** Keep top-level batches capped at 40 items. Move one-batch translation into an async helper that validates ordered IDs, returns valid translations directly, splits mismatched multi-item batches into ordered halves, and raises the existing subtitle error only for a mismatched single-item batch.

**Tech Stack:** Python 3.13, Pydantic v2, pytest, pytest-asyncio, Ruff.

---

### Task 1: Adaptive Batch Splitting

**Files:**
- Modify: `src/insightcast/engines/lingo_engine.py`
- Test: `tests/unit/test_lingo_engine.py`

- [x] **Step 1: Add a failing 40-to-20 retry test**

Configure a 40-item response that omits its last two IDs, followed by two valid
20-item responses. Assert request sizes are `[40, 20, 20]` and final subtitle IDs
remain ordered.

- [x] **Step 2: Verify RED**

Run:

```bash
uv run pytest tests/unit/test_lingo_engine.py -k splits_mismatched -q
```

Expected: FAIL because the first mismatch terminates translation.

- [x] **Step 3: Add a failing terminal single-item diagnostic test**

Return no translation for one source item. Assert the error contains top-level
`batch_index`, an empty `batch_path`, and exact source/translation IDs.

- [x] **Step 4: Implement recursive ordered splitting**

Add `_translate_batch` to translate one batch, validate exact IDs, recursively
translate ordered halves on mismatch, and raise only when a single item still
mismatches.

- [x] **Step 5: Verify focused tests**

Run:

```bash
uv run pytest tests/unit/test_lingo_engine.py -q
```

Expected: PASS.

### Task 2: Repository Verification

**Files:**
- Verify: `src/insightcast/engines/lingo_engine.py`
- Verify: `tests/unit/test_lingo_engine.py`

- [x] **Step 1: Run Ruff**

```bash
uv run ruff check .
```

- [x] **Step 2: Run full tests**

```bash
uv run pytest -q
```

- [x] **Step 3: Check diff**

```bash
git diff --check
git status --short
```
