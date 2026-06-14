# Curator Candidate Duration Normalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Normalize model-selected candidate ranges to complete transcript segments while preferring 8-12 minute windows and accepting bounded 7-13 minute fallbacks.

**Architecture:** Add a deterministic normalization step inside `CuratorEngine` before validation. The normalizer preserves candidate metadata, aligns times to transcript segment boundaries, expands or contracts continuous windows toward the target duration, and returns detailed validation feedback when normalization is impossible. The prompt remains data-only but exposes target and accepted duration bounds and clarifies that model times are approximate semantic selections.

**Tech Stack:** Python 3.13, Pydantic v2, pytest, pytest-asyncio, Ruff.

---

### Task 1: Segment-Aligned Candidate Normalization

**Files:**
- Modify: `src/insightcast/engines/curator_engine.py`
- Test: `tests/unit/test_curator_engine.py`

- [x] **Step 1: Add failing normalization tests**

Add focused tests for:

```python
def segmented_transcript(*bounds: tuple[float, float]) -> Transcript:
    return Transcript(
        language="en",
        duration_seconds=bounds[-1][1],
        segments=[
            TranscriptSegment(
                segment_id=f"s{index}",
                start_seconds=start,
                end_seconds=end,
                text=f"Segment {index}",
            )
            for index, (start, end) in enumerate(bounds, start=1)
        ],
    )
```

Cover an already aligned candidate, boundaries inside segments, undersized expansion, tolerance expansion, oversized contraction, tolerance contraction, transcript-edge expansion, metadata preservation, no transcript overlap, and no possible accepted window.

- [x] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest tests/unit/test_curator_engine.py -q
```

Expected: new assertions fail because candidates still retain model-provided boundaries or retry instead of normalizing.

- [x] **Step 3: Implement minimal deterministic normalization**

In `CuratorEngine`:

```python
ACCEPTED_DURATION_TOLERANCE_SECONDS = 60
```

Add helpers that:

- identify transcript segments overlapping the proposed range;
- create an initial continuous segment window;
- expand short windows by the adjacent boundary closest to the original range;
- contract long windows by removing the outer segment with the least overlap with the original range;
- prefer the configured target range;
- retain a 60-second-bounded tolerance window when whole-segment adjustment cannot reach the target;
- return `None` when the candidate does not overlap the transcript or no accepted window exists;
- copy all descriptive fields unchanged while replacing only `start_seconds` and `end_seconds`.

Call normalization before `_validate_candidates`, then validate against the accepted minimum and maximum.

- [x] **Step 4: Run curator tests to verify they pass**

Run:

```bash
uv run pytest tests/unit/test_curator_engine.py -q
```

Expected: PASS.

### Task 2: Retry Feedback And Prompt Contract

**Files:**
- Modify: `src/insightcast/engines/curator_engine.py`
- Modify: `src/insightcast/prompts/curator.py`
- Test: `tests/unit/test_curator_engine.py`
- Test: `tests/unit/test_prompts.py`

- [x] **Step 1: Add failing feedback and prompt tests**

Assert that:

```python
assert curator.PROMPT_VERSION == "curator-v2"
assert '"target_min_duration_seconds": 480' in curator_user
assert '"target_max_duration_seconds": 720' in curator_user
assert '"accepted_min_duration_seconds": 420' in curator_user
assert '"accepted_max_duration_seconds": 780' in curator_user
assert '"times_are_approximate": true' in curator_user
assert '"prefer_complete_idea_arcs": true' in curator_user
```

For an impossible candidate, assert retry feedback includes candidate ID, actual duration, target range, and accepted range.

- [x] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest tests/unit/test_curator_engine.py tests/unit/test_prompts.py -q
```

Expected: prompt version and payload assertions fail, and retry feedback lacks detailed duration diagnostics.

- [x] **Step 3: Implement prompt and feedback changes**

Change the prompt builder to accept seconds:

```python
def build_user_prompt(
    *,
    transcript: Sequence[Mapping[str, Any]],
    candidate_count: int,
    target_min_duration_seconds: float,
    target_max_duration_seconds: float,
    accepted_min_duration_seconds: float,
    accepted_max_duration_seconds: float,
    validation_feedback: str | None,
) -> str:
```

Set `PROMPT_VERSION = "curator-v2"` and include explicit approximate-time and complete-idea-arc flags in the JSON payload. Update validation feedback to report actual, target, and accepted durations.

- [x] **Step 4: Run focused tests to verify they pass**

Run:

```bash
uv run pytest tests/unit/test_curator_engine.py tests/unit/test_prompts.py -q
```

Expected: PASS.

### Task 3: Regression Verification

**Files:**
- Verify: `src/insightcast/engines/curator_engine.py`
- Verify: `src/insightcast/prompts/curator.py`
- Verify: `tests/unit/test_curator_engine.py`
- Verify: `tests/unit/test_prompts.py`

- [x] **Step 1: Run formatting and static checks**

Run:

```bash
uv run ruff check src/insightcast/engines/curator_engine.py src/insightcast/prompts/curator.py tests/unit/test_curator_engine.py tests/unit/test_prompts.py
```

Expected: PASS with no diagnostics.

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

Expected: no whitespace errors; only the implementation plan, curator engine, prompt, and focused tests are modified.
