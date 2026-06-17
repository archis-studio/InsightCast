# Windowed Candidate Boundary Selection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce `candidate_boundary_selection` prompt size by sending only original transcript windows around discovered topics while preserving full-transcript candidate normalization.

**Architecture:** Keep `topic_discovery` unchanged and full-transcript. Add a focused window builder in `src/insightcast/engines/curator_engine.py`, then have `select_candidates()` pass windowed original segments into the candidate prompt while continuing to normalize and validate against the full transcript. Update the candidate prompt contract to explicitly say the transcript is selected source windows, not the full transcript.

**Tech Stack:** Python 3.13, Pydantic models, pytest, existing Insight Cast curator and prompt modules.

---

## File Structure

- Modify `src/insightcast/engines/curator_engine.py`
  - Add `_build_topic_windows()` and small private helpers for valid topic ranges, window expansion, merging, and segment selection.
  - Change `select_candidates()` to use windowed segments for `curator.build_user_prompt()`.
  - Keep `_normalize_candidates()` and `_validate_candidates()` operating on the complete transcript.
- Modify `src/insightcast/prompts/curator.py`
  - Add explicit `transcript_scope` and `transcript_is_complete` fields.
  - Adjust wording so the system prompt does not imply the transcript payload is complete.
- Modify `tests/unit/test_curator_engine.py`
  - Add direct unit tests for window construction.
  - Add integration-style unit tests around `select_candidates()` prompt input and fallback.
- Modify `tests/unit/test_prompts.py`
  - Add assertions for the new prompt scope fields.

## Task 1: Add Window Builder Tests

**Files:**
- Modify: `tests/unit/test_curator_engine.py`
- Later implementation target: `src/insightcast/engines/curator_engine.py`

- [ ] **Step 1: Add failing tests for normal window construction**

Append these tests after `test_curator_retries_once_with_validation_feedback` in `tests/unit/test_curator_engine.py`:

```python
def test_build_topic_windows_adds_context_and_clamps_to_transcript() -> None:
    source = segmented_transcript(
        (0, 60),
        (60, 120),
        (120, 180),
        (180, 240),
        (240, 300),
        (300, 360),
        (360, 420),
        (420, 480),
        (480, 540),
        (540, 600),
        (600, 660),
        (660, 720),
        (720, 780),
        (780, 840),
        (840, 900),
    )

    windowed = curator_engine._build_topic_windows(
        segments=source.segments,
        topics=[topic("T1", 300, 360, 0.9)],
        target_min_duration_seconds=480,
        final_max_duration_seconds=810,
    )

    assert [segment.segment_id for segment in windowed] == [
        "s1",
        "s2",
        "s3",
        "s4",
        "s5",
        "s6",
        "s7",
        "s8",
        "s9",
        "s10",
        "s11",
        "s12",
    ]


def test_build_topic_windows_merges_overlaps_and_preserves_order() -> None:
    source = segmented_transcript(
        *[(second, second + 60) for second in range(0, 1800, 60)]
    )

    windowed = curator_engine._build_topic_windows(
        segments=source.segments,
        topics=[
            topic("T1", 300, 420, 0.9),
            topic("T2", 600, 720, 0.8),
        ],
        target_min_duration_seconds=480,
        final_max_duration_seconds=810,
    )

    ids = [segment.segment_id for segment in windowed]
    assert ids == [f"s{index}" for index in range(1, 18)]
    assert len(ids) == len(set(ids))
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run:

```bash
uv run pytest tests/unit/test_curator_engine.py::test_build_topic_windows_adds_context_and_clamps_to_transcript tests/unit/test_curator_engine.py::test_build_topic_windows_merges_overlaps_and_preserves_order -q
```

Expected: FAIL with `AttributeError: module 'insightcast.engines.curator_engine' has no attribute '_build_topic_windows'`.

- [ ] **Step 3: Commit the failing tests**

Run:

```bash
git add tests/unit/test_curator_engine.py
git commit -m "test: cover curator topic window construction"
```

## Task 2: Implement Topic Window Construction

**Files:**
- Modify: `src/insightcast/engines/curator_engine.py`
- Test: `tests/unit/test_curator_engine.py`

- [ ] **Step 1: Update imports**

Change the imports at the top of `src/insightcast/engines/curator_engine.py` from:

```python
import math
from typing import Any
```

to:

```python
import math
from collections.abc import Sequence
from typing import Any
```

- [ ] **Step 2: Add window constants**

Add these constants under `TOPIC_POOL_MULTIPLIER = 2`:

```python
TOPIC_PRE_BUFFER_SECONDS = 120
TOPIC_POST_BUFFER_SECONDS = 180
```

- [ ] **Step 3: Add window helper implementation**

Add this code above `_normalize_candidate()`:

```python
def _build_topic_windows(
    *,
    segments: Sequence[TranscriptSegment],
    topics: Sequence[TopicDiscoveryOutput],
    target_min_duration_seconds: float,
    final_max_duration_seconds: float,
) -> list[TranscriptSegment]:
    if not segments:
        return []

    transcript_start = segments[0].start_seconds
    transcript_end = segments[-1].end_seconds
    windows: list[tuple[float, float]] = []
    pre_buffer_seconds = max(
        TOPIC_PRE_BUFFER_SECONDS,
        target_min_duration_seconds / 4,
    )
    post_buffer_seconds = max(
        TOPIC_POST_BUFFER_SECONDS,
        target_min_duration_seconds / 4,
    )

    for topic in topics:
        if not _is_valid_topic_range(topic):
            continue
        start = max(transcript_start, topic.start_seconds - pre_buffer_seconds)
        end = min(transcript_end, topic.end_seconds + post_buffer_seconds)
        start, end = _expand_window_to_duration(
            start,
            end,
            minimum_duration_seconds=final_max_duration_seconds,
            transcript_start=transcript_start,
            transcript_end=transcript_end,
        )
        if end > start:
            windows.append((start, end))

    if not windows:
        return []

    merged = _merge_time_windows(windows)
    return [
        segment
        for segment in segments
        if any(
            segment.end_seconds > start and segment.start_seconds < end
            for start, end in merged
        )
    ]


def _is_valid_topic_range(topic: TopicDiscoveryOutput) -> bool:
    return (
        math.isfinite(topic.start_seconds)
        and math.isfinite(topic.end_seconds)
        and topic.start_seconds >= 0
        and topic.end_seconds > topic.start_seconds
    )


def _expand_window_to_duration(
    start: float,
    end: float,
    *,
    minimum_duration_seconds: float,
    transcript_start: float,
    transcript_end: float,
) -> tuple[float, float]:
    available_duration = transcript_end - transcript_start
    target_duration = min(minimum_duration_seconds, available_duration)
    current_duration = end - start
    if current_duration >= target_duration:
        return start, end

    missing = target_duration - current_duration
    expanded_start = max(transcript_start, start - missing / 2)
    expanded_end = min(transcript_end, end + missing / 2)

    remaining = target_duration - (expanded_end - expanded_start)
    if remaining > 0 and expanded_start == transcript_start:
        expanded_end = min(transcript_end, expanded_end + remaining)
    elif remaining > 0 and expanded_end == transcript_end:
        expanded_start = max(transcript_start, expanded_start - remaining)

    return expanded_start, expanded_end


def _merge_time_windows(windows: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    ordered = sorted(windows)
    merged: list[tuple[float, float]] = []
    for start, end in ordered:
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
            continue
        prior_start, prior_end = merged[-1]
        merged[-1] = (prior_start, max(prior_end, end))
    return merged
```

- [ ] **Step 4: Run window tests**

Run:

```bash
uv run pytest tests/unit/test_curator_engine.py::test_build_topic_windows_adds_context_and_clamps_to_transcript tests/unit/test_curator_engine.py::test_build_topic_windows_merges_overlaps_and_preserves_order -q
```

Expected: PASS.

- [ ] **Step 5: Commit implementation**

Run:

```bash
git add src/insightcast/engines/curator_engine.py
git commit -m "feat: build transcript windows around topics"
```

## Task 3: Add Edge Case Window Tests

**Files:**
- Modify: `tests/unit/test_curator_engine.py`
- Modify later: `src/insightcast/engines/curator_engine.py`

- [ ] **Step 1: Add edge case tests**

Append these tests after the Task 1 window tests:

```python
def test_build_topic_windows_skips_invalid_topic_ranges() -> None:
    source = segmented_transcript(
        (0, 100),
        (100, 200),
        (200, 300),
        (300, 400),
        (400, 500),
        (500, 600),
    )

    windowed = curator_engine._build_topic_windows(
        segments=source.segments,
        topics=[
            topic("T1", float("nan"), 100, 0.9),
            topic("T2", 400, 300, 0.8),
            topic("T3", 200, 300, 0.7),
        ],
        target_min_duration_seconds=480,
        final_max_duration_seconds=810,
    )

    assert [segment.segment_id for segment in windowed] == [
        "s1",
        "s2",
        "s3",
        "s4",
        "s5",
        "s6",
    ]


def test_build_topic_windows_returns_empty_for_no_valid_ranges() -> None:
    source = segmented_transcript((0, 100), (100, 200))

    windowed = curator_engine._build_topic_windows(
        segments=source.segments,
        topics=[
            topic("T1", float("inf"), 100, 0.9),
            topic("T2", 150, 150, 0.8),
        ],
        target_min_duration_seconds=480,
        final_max_duration_seconds=810,
    )

    assert windowed == []
```

- [ ] **Step 2: Run edge case tests**

Run:

```bash
uv run pytest tests/unit/test_curator_engine.py::test_build_topic_windows_skips_invalid_topic_ranges tests/unit/test_curator_engine.py::test_build_topic_windows_returns_empty_for_no_valid_ranges -q
```

Expected: PASS if Task 2 was implemented correctly.

- [ ] **Step 3: Commit edge case tests**

Run:

```bash
git add tests/unit/test_curator_engine.py
git commit -m "test: cover curator topic window edge cases"
```

## Task 4: Use Windowed Transcript in Candidate Selection

**Files:**
- Modify: `tests/unit/test_curator_engine.py`
- Modify: `src/insightcast/engines/curator_engine.py`

- [ ] **Step 1: Add failing select-candidates prompt test**

Append this test after `test_curator_accepts_exact_ordered_candidates_and_overlap`:

```python
@pytest.mark.asyncio
async def test_select_candidates_sends_windowed_transcript_to_boundary_prompt() -> None:
    source = segmented_transcript(
        *[(second, second + 60) for second in range(0, 2400, 60)]
    )
    client = FakeStructuredClient(
        [CuratorResponse(candidates=[output("A", 300, 900)])]
    )

    await CuratorEngine(client=client, model="gpt-curator").select_candidates(
        transcript=source,
        topics=TopicDiscoveryResponse(
            topics=[topic("T1", 600, 660, 0.9)]
        ),
        candidate_count=1,
        min_duration_minutes=8,
        max_duration_minutes=12,
    )

    prompt = str(client.calls[0]["user_prompt"])
    assert '"segment_id": "s1"' not in prompt
    assert '"segment_id": "s6"' in prompt
    assert '"segment_id": "s20"' not in prompt
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
uv run pytest tests/unit/test_curator_engine.py::test_select_candidates_sends_windowed_transcript_to_boundary_prompt -q
```

Expected: FAIL because the current prompt still includes all transcript segments.

- [ ] **Step 3: Implement windowed prompt input**

In `select_candidates()`, immediately before the `for attempt in range(2):` loop, add:

```python
        windowed_segments = _build_topic_windows(
            segments=transcript.segments,
            topics=topics.topics,
            target_min_duration_seconds=target_min_duration_seconds,
            final_max_duration_seconds=final_max_duration_seconds,
        )
        prompt_segments = windowed_segments or transcript.segments
```

Then change the `curator.build_user_prompt()` call from:

```python
                    transcript=[
                        segment.model_dump(mode="json") for segment in transcript.segments
                    ],
```

to:

```python
                    transcript=[
                        segment.model_dump(mode="json") for segment in prompt_segments
                    ],
```

- [ ] **Step 4: Run the new test**

Run:

```bash
uv run pytest tests/unit/test_curator_engine.py::test_select_candidates_sends_windowed_transcript_to_boundary_prompt -q
```

Expected: PASS.

- [ ] **Step 5: Commit windowed selection**

Run:

```bash
git add tests/unit/test_curator_engine.py src/insightcast/engines/curator_engine.py
git commit -m "feat: use topic windows for candidate prompts"
```

## Task 5: Preserve Full Transcript Fallback and Normalization

**Files:**
- Modify: `tests/unit/test_curator_engine.py`

- [ ] **Step 1: Add fallback and normalization tests**

Append these tests after the Task 4 test:

```python
@pytest.mark.asyncio
async def test_select_candidates_falls_back_to_full_transcript_when_windows_are_empty() -> None:
    source = segmented_transcript((0, 300), (300, 600), (600, 900))
    client = FakeStructuredClient(
        [CuratorResponse(candidates=[output("A", 0, 600)])]
    )

    await CuratorEngine(client=client, model="gpt-curator").select_candidates(
        transcript=source,
        topics=TopicDiscoveryResponse(
            topics=[topic("T1", float("nan"), 600, 0.9)]
        ),
        candidate_count=1,
        min_duration_minutes=8,
        max_duration_minutes=12,
    )

    prompt = str(client.calls[0]["user_prompt"])
    assert '"segment_id": "s1"' in prompt
    assert '"segment_id": "s2"' in prompt
    assert '"segment_id": "s3"' in prompt


@pytest.mark.asyncio
async def test_select_candidates_normalizes_against_full_transcript_not_window() -> None:
    source = segmented_transcript(
        *[(second, second + 60) for second in range(0, 1800, 60)]
    )
    client = FakeStructuredClient(
        [CuratorResponse(candidates=[output("A", 60, 420)])]
    )

    result = await CuratorEngine(client=client, model="gpt-curator").select_candidates(
        transcript=source,
        topics=TopicDiscoveryResponse(
            topics=[topic("T1", 300, 360, 0.9)]
        ),
        candidate_count=1,
        min_duration_minutes=8,
        max_duration_minutes=12,
    )

    candidate = result.candidates[0]
    assert (candidate.start_seconds, candidate.end_seconds) == (60, 540)
```

- [ ] **Step 2: Run fallback and normalization tests**

Run:

```bash
uv run pytest tests/unit/test_curator_engine.py::test_select_candidates_falls_back_to_full_transcript_when_windows_are_empty tests/unit/test_curator_engine.py::test_select_candidates_normalizes_against_full_transcript_not_window -q
```

Expected: PASS.

- [ ] **Step 3: Run curator unit suite**

Run:

```bash
uv run pytest tests/unit/test_curator_engine.py -q
```

Expected: PASS.

- [ ] **Step 4: Commit tests**

Run:

```bash
git add tests/unit/test_curator_engine.py
git commit -m "test: preserve candidate fallback and normalization"
```

## Task 6: Update Candidate Prompt Contract

**Files:**
- Modify: `src/insightcast/prompts/curator.py`
- Modify: `tests/unit/test_prompts.py`

- [ ] **Step 1: Add failing prompt assertions**

In `tests/unit/test_prompts.py`, find the existing curator prompt assertions near the top of the file. Add:

```python
    assert curator_payload["transcript_scope"] == "selected_source_windows_around_ranked_topics"
    assert curator_payload["transcript_is_complete"] is False
```

inside the same test after `curator_payload = json.loads(curator_user)`.

- [ ] **Step 2: Run prompt test to verify failure**

Run:

```bash
uv run pytest tests/unit/test_prompts.py -q
```

Expected: FAIL with `KeyError: 'transcript_scope'`.

- [ ] **Step 3: Update system prompt wording**

In `src/insightcast/prompts/curator.py`, replace `SYSTEM_PROMPT` with:

```python
SYSTEM_PROMPT = """You are the candidate-boundary stage of a knowledge-video curator.
Select the most important distinct knowledge units from ranked topic-centered transcript windows.
Choose continuous source ranges that preserve necessary background, the central claim or finding,
key evidence or reasoning, and a meaningful conclusion. Remove greetings, sponsorships,
repetition, and tangents when they are not needed for the argument. Return only the requested
structured output."""
```

- [ ] **Step 4: Add prompt payload fields**

In `build_user_prompt()`, add these fields immediately before `"transcript": list(transcript),`:

```python
        "transcript_scope": "selected_source_windows_around_ranked_topics",
        "transcript_is_complete": False,
```

- [ ] **Step 5: Run prompt tests**

Run:

```bash
uv run pytest tests/unit/test_prompts.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit prompt contract**

Run:

```bash
git add src/insightcast/prompts/curator.py tests/unit/test_prompts.py
git commit -m "feat: mark candidate prompts as windowed source context"
```

## Task 7: Final Verification

**Files:**
- Verify: `src/insightcast/engines/curator_engine.py`
- Verify: `src/insightcast/prompts/curator.py`
- Verify: `tests/unit/test_curator_engine.py`
- Verify: `tests/unit/test_prompts.py`

- [ ] **Step 1: Run focused tests**

Run:

```bash
uv run pytest tests/unit/test_curator_engine.py tests/unit/test_prompts.py -q
```

Expected: PASS.

- [ ] **Step 2: Run lint on touched Python files**

Run:

```bash
uv run ruff check src/insightcast/engines/curator_engine.py src/insightcast/prompts/curator.py tests/unit/test_curator_engine.py tests/unit/test_prompts.py
```

Expected: PASS.

- [ ] **Step 3: Check whitespace**

Run:

```bash
git diff --check
```

Expected: no output and exit code 0.

- [ ] **Step 4: Inspect final diff**

Run:

```bash
git diff --stat HEAD~5..HEAD
git diff HEAD~5..HEAD -- src/insightcast/engines/curator_engine.py src/insightcast/prompts/curator.py tests/unit/test_curator_engine.py tests/unit/test_prompts.py
```

Expected: diff shows only the windowed boundary selection implementation, prompt contract update, and related tests.

- [ ] **Step 5: Optional real-video acceptance**

Only if `cast_api` is already running separately, queue a forced analysis for the known failing URL and monitor it:

```bash
uv run python -c 'import json, urllib.request; data=json.dumps({"youtube_url":"https://youtu.be/uRQAhWZ1bxs?si=3CLaZRSDunmyJvZ-","force_reanalyze":True}).encode(); req=urllib.request.Request("http://127.0.0.1:8765/api/v1/analysis-jobs", data=data, headers={"Content-Type":"application/json"}, method="POST"); print(urllib.request.urlopen(req, timeout=10).read().decode())'
uv run cast_analyze "https://youtu.be/uRQAhWZ1bxs?si=3CLaZRSDunmyJvZ-"
```

Expected: the analysis reaches `WAITING_SELECTION`, or any remaining `LLM_REQUEST_FAILED` has a lower requested-token footprint than the previous full-transcript second stage.

- [ ] **Step 6: Commit any final verification-only adjustments**

If lint or tests required small fixes, commit them:

```bash
git add src/insightcast/engines/curator_engine.py src/insightcast/prompts/curator.py tests/unit/test_curator_engine.py tests/unit/test_prompts.py
git commit -m "fix: polish windowed boundary selection"
```

Skip this commit if there are no changes after verification.
