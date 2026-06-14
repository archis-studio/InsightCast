# Retry Unreadable Subtitle Translations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Retry translation batches when any returned subtitle text is empty or punctuation-only.

**Architecture:** Reuse adaptive batch splitting for both ID mismatches and unreadable text. A valid batch must have exact ordered IDs and readable text for every item; otherwise multi-item batches split recursively, while a terminal single-item failure reports its batch location, segment ID, and returned text.

**Tech Stack:** Python 3.13, Pydantic v2, pytest, pytest-asyncio, Ruff.

---

### Task 1: Retry Unreadable Items

**Files:**
- Modify: `src/insightcast/engines/lingo_engine.py`
- Test: `tests/unit/test_lingo_engine.py`

- [x] Add a failing test where a two-item response contains punctuation-only text,
  then succeeds as two one-item retries.
- [x] Add a failing test where a single-item response remains punctuation-only and
  must report batch and translation diagnostics.
- [x] Verify the tests fail because readability is checked only after batching.
- [x] Extract one readability predicate and use it in batch acceptance and final
  subtitle preparation.
- [x] Split unreadable multi-item batches and fail unreadable single-item batches.
- [x] Run `uv run pytest tests/unit/test_lingo_engine.py -q`.

### Task 2: Repository Verification

**Files:**
- Verify: `src/insightcast/engines/lingo_engine.py`
- Verify: `tests/unit/test_lingo_engine.py`

- [x] Run `uv run ruff check .`.
- [x] Run `uv run pytest -q`.
- [x] Run `git diff --check`.
