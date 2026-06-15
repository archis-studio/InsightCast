# Two-Stage Topic Curation And Knowledge-News Metadata Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rank the most important distinct topics across a full YouTube transcript before selecting complete candidate ranges, and generate evidence-grounded Traditional Chinese knowledge-news metadata for rendered highlights.

**Architecture:** Split curation into `discover_topics()` and `select_candidates()` while retaining `curate()` as a compatibility wrapper. Topic discovery uses a dedicated versioned prompt and internal Pydantic contract; candidate selection receives the ranked topic pool, applies the existing segment normalizer with a new three-tier duration policy, and persists only the unchanged public `Candidate` contract. `JobService` runs the two stages separately for operation-log visibility, while metadata remains a render-time prompt-only change.

**Tech Stack:** Python 3.13, Pydantic v2, OpenAI structured responses, pytest, pytest-asyncio, Ruff.

---

## File Structure

- Create `src/insightcast/prompts/topic_discovery.py`: versioned prompt contract for finding and ranking distinct important topics.
- Modify `src/insightcast/prompts/curator.py`: second-stage candidate-boundary prompt that consumes discovered topics and all three duration ranges.
- Modify `src/insightcast/prompts/metadata.py`: knowledge-news Traditional Chinese metadata instructions and disclosure rules.
- Modify `src/insightcast/engines/curator_engine.py`: internal topic models, discovery validation/retry, second-stage selection, combined prompt trace, and final duration normalization.
- Modify `src/insightcast/services/job_service.py`: execute and log topic discovery and candidate boundary selection as separate stages.
- Modify focused unit and service tests; public domain, API, manifest, storage, and render schemas remain unchanged.

### Task 1: Topic Discovery Prompt Contract

**Files:**
- Create: `src/insightcast/prompts/topic_discovery.py`
- Modify: `tests/unit/test_prompts.py`

- [ ] **Step 1: Write the failing topic-discovery prompt test**

Add this focused test to `tests/unit/test_prompts.py`:

```python
from insightcast.prompts import curator, metadata, topic_discovery, translation


def test_topic_discovery_prompt_ranks_distinct_important_topics() -> None:
    prompt = topic_discovery.build_user_prompt(
        transcript=[
            {
                "segment_id": "s1",
                "start_seconds": 0,
                "end_seconds": 30,
                "text": "A central finding",
            }
        ],
        topic_pool_size=4,
        validation_feedback=None,
    )
    payload = json.loads(prompt)

    assert topic_discovery.PROMPT_VERSION == "topic-discovery-v1"
    assert payload["topic_pool_size"] == 4
    assert payload["evaluate_full_transcript"] is True
    assert payload["rank_by_importance"] is True
    assert payload["require_distinct_topics"] is True
    assert payload["exclude_low_value_material"] == [
        "greetings",
        "sponsorships",
        "repetition",
        "anecdotes_without_a_broader_point",
        "setup_without_a_conclusion",
    ]
    system = topic_discovery.SYSTEM_PROMPT.lower()
    assert "full transcript" in system
    assert "importance" in system
    assert "distinct" in system
    assert "controvers" in system
```

Update the existing combined import so it includes `topic_discovery`.

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
uv run pytest tests/unit/test_prompts.py::test_topic_discovery_prompt_ranks_distinct_important_topics -q
```

Expected: FAIL with an import error because `topic_discovery.py` does not exist.

- [ ] **Step 3: Implement the topic-discovery prompt**

Create `src/insightcast/prompts/topic_discovery.py`:

```python
import json
from collections.abc import Mapping, Sequence
from typing import Any

PROMPT_VERSION = "topic-discovery-v1"
SYSTEM_PROMPT = """You are the topic-discovery stage of a knowledge-video curator.
Evaluate the full transcript before ranking topics. Identify distinct claims, findings,
explanations, consequences, or decisions with lasting knowledge value. Merge semantic
duplicates. Do not rank a topic highly merely because it is controversial or emotionally
phrased. Return only the requested structured output."""


def build_user_prompt(
    *,
    transcript: Sequence[Mapping[str, Any]],
    topic_pool_size: int,
    validation_feedback: str | None,
) -> str:
    return json.dumps(
        {
            "topic_pool_size": topic_pool_size,
            "evaluate_full_transcript": True,
            "rank_by_importance": True,
            "require_distinct_topics": True,
            "exclude_low_value_material": [
                "greetings",
                "sponsorships",
                "repetition",
                "anecdotes_without_a_broader_point",
                "setup_without_a_conclusion",
            ],
            "topic_requirements": (
                "Return sequential topic IDs in descending importance order. "
                "For each topic provide a concise label, summary, central claim, "
                "reason it matters, approximate continuous time range, and importance score."
            ),
            "transcript": list(transcript),
            "validation_feedback": validation_feedback,
        },
        ensure_ascii=False,
        indent=2,
    )
```

- [ ] **Step 4: Run the prompt test to verify it passes**

Run:

```bash
uv run pytest tests/unit/test_prompts.py::test_topic_discovery_prompt_ranks_distinct_important_topics -q
```

Expected: PASS.

- [ ] **Step 5: Commit the prompt contract**

```bash
git add src/insightcast/prompts/topic_discovery.py tests/unit/test_prompts.py
git commit -m "feat: add topic discovery prompt"
```

### Task 2: Topic Discovery Models, Validation, And Retry

**Files:**
- Modify: `src/insightcast/engines/curator_engine.py`
- Modify: `tests/unit/test_curator_engine.py`

- [ ] **Step 1: Add topic fixtures and failing discovery tests**

In `tests/unit/test_curator_engine.py`, import the new models and add:

```python
from insightcast.engines.curator_engine import (
    CuratorCandidateOutput,
    CuratorEngine,
    CuratorResponse,
    TopicDiscoveryOutput,
    TopicDiscoveryResponse,
)


def topic(
    topic_id: str,
    start: float,
    end: float,
    score: float,
) -> TopicDiscoveryOutput:
    return TopicDiscoveryOutput(
        topic_id=topic_id,
        label=f"Topic {topic_id}",
        summary=f"Summary {topic_id}",
        central_claim=f"Claim {topic_id}",
        importance_reason=f"Reason {topic_id}",
        start_seconds=start,
        end_seconds=end,
        importance_score=score,
    )


@pytest.mark.asyncio
async def test_discover_topics_requests_larger_ranked_pool() -> None:
    response = TopicDiscoveryResponse(
        topics=[
            topic("T1", 0, 300, 0.95),
            topic("T2", 300, 600, 0.90),
            topic("T3", 600, 900, 0.85),
            topic("T4", 900, 1200, 0.80),
        ]
    )
    client = FakeStructuredClient([response])
    engine = CuratorEngine(client=client, model="gpt-curator")

    result = await engine.discover_topics(
        transcript=transcript(),
        candidate_count=2,
    )

    assert [item.topic_id for item in result.topics] == ["T1", "T2", "T3", "T4"]
    assert '"topic_pool_size": 4' in str(client.calls[0]["user_prompt"])
    assert client.calls[0]["response_model"] is TopicDiscoveryResponse


@pytest.mark.asyncio
async def test_discover_topics_retries_with_specific_validation_feedback() -> None:
    invalid = TopicDiscoveryResponse(
        topics=[
            topic("T2", 0, 300, 0.80),
            topic("T1", 300, 600, 0.90),
        ]
    )
    valid = TopicDiscoveryResponse(
        topics=[
            topic("T1", 0, 300, 0.95),
            topic("T2", 300, 600, 0.90),
            topic("T3", 600, 900, 0.85),
            topic("T4", 900, 1200, 0.80),
        ]
    )
    client = FakeStructuredClient([invalid, valid])

    result = await CuratorEngine(client=client, model="gpt-curator").discover_topics(
        transcript=transcript(),
        candidate_count=2,
    )

    assert len(result.topics) == 4
    retry_prompt = str(client.calls[1]["user_prompt"])
    assert "topic pool must contain at least 3 topics" in retry_prompt
    assert "topic 1 ID must be T1" in retry_prompt
    assert "descending importance order" in retry_prompt
```

Generalize `FakeStructuredClient.responses` and `parse()` return annotation to accept
`BaseModel`, because it now returns two structured response types:

```python
from pydantic import BaseModel


class FakeStructuredClient:
    def __init__(self, responses: list[BaseModel]) -> None:
        self.responses = responses
        self.calls: list[dict[str, object]] = []

    async def parse(self, **kwargs: object) -> BaseModel:
        self.calls.append(kwargs)
        return self.responses.pop(0)
```

- [ ] **Step 2: Run discovery tests to verify they fail**

Run:

```bash
uv run pytest \
  tests/unit/test_curator_engine.py::test_discover_topics_requests_larger_ranked_pool \
  tests/unit/test_curator_engine.py::test_discover_topics_retries_with_specific_validation_feedback \
  -q
```

Expected: FAIL because topic models and `discover_topics()` are undefined.

- [ ] **Step 3: Implement topic models and discovery validation**

In `src/insightcast/engines/curator_engine.py`, add:

```python
from insightcast.prompts import curator, topic_discovery

TOPIC_POOL_MULTIPLIER = 2


class TopicDiscoveryOutput(CuratorModel):
    topic_id: str
    label: str
    summary: str
    central_claim: str
    importance_reason: str
    start_seconds: float
    end_seconds: float
    importance_score: float


class TopicDiscoveryResponse(CuratorModel):
    topics: list[TopicDiscoveryOutput]
```

Add this method to `CuratorEngine`:

```python
async def discover_topics(
    self,
    *,
    transcript: Transcript,
    candidate_count: int,
) -> TopicDiscoveryResponse:
    topic_pool_size = candidate_count * TOPIC_POOL_MULTIPLIER
    minimum_topic_count = candidate_count + 1
    feedback: str | None = None
    last_response: TopicDiscoveryResponse | None = None
    for attempt in range(2):
        response = await self.client.parse(
            model=self.model,
            system_prompt=topic_discovery.SYSTEM_PROMPT,
            user_prompt=topic_discovery.build_user_prompt(
                transcript=[
                    segment.model_dump(mode="json") for segment in transcript.segments
                ],
                topic_pool_size=topic_pool_size,
                validation_feedback=feedback,
            ),
            response_model=TopicDiscoveryResponse,
        )
        last_response = response
        errors = self._validate_topics(
            response.topics,
            minimum_topic_count=minimum_topic_count,
            transcript_duration=transcript.duration_seconds,
        )
        if not errors:
            return response
        feedback = "; ".join(errors)
        if attempt == 1:
            break

    assert last_response is not None
    if len(last_response.topics) < minimum_topic_count:
        raise InsightCastError(
            ErrorCode.INSUFFICIENT_CANDIDATES,
            "The curator could not discover enough valid topics.",
            details={
                "minimum_topics": minimum_topic_count,
                "requested_topic_pool": topic_pool_size,
                "received_topics": len(last_response.topics),
                "validation_feedback": feedback,
            },
            stage="topic_discovery",
        )
    raise InsightCastError(
        ErrorCode.INVALID_LLM_OUTPUT,
        "The curator returned invalid topic discovery data after one retry.",
        details={"validation_feedback": feedback},
        stage="topic_discovery",
    )

@staticmethod
def _validate_topics(
    topics: list[TopicDiscoveryOutput],
    *,
    minimum_topic_count: int,
    transcript_duration: float,
) -> list[str]:
    errors: list[str] = []
    if len(topics) < minimum_topic_count:
        errors.append(
            f"topic pool must contain at least {minimum_topic_count} topics, "
            f"received {len(topics)}"
        )
    previous_score: float | None = None
    for index, topic in enumerate(topics):
        expected_id = f"T{index + 1}"
        if topic.topic_id != expected_id:
            errors.append(
                f"topic {index + 1} ID must be {expected_id}, received {topic.topic_id}"
            )
        for field_name in (
            "label",
            "summary",
            "central_claim",
            "importance_reason",
        ):
            if not getattr(topic, field_name).strip():
                errors.append(f"topic {topic.topic_id} {field_name} must not be empty")
        if (
            topic.start_seconds < 0
            or topic.end_seconds <= topic.start_seconds
            or topic.end_seconds > transcript_duration
        ):
            errors.append(f"topic {topic.topic_id} has an invalid time range")
        if not 0 <= topic.importance_score <= 1:
            errors.append(
                f"topic {topic.topic_id} importance_score must be between 0 and 1"
            )
        if previous_score is not None and topic.importance_score > previous_score:
            errors.append("topics must be in descending importance order")
        previous_score = topic.importance_score
    return errors
```

- [ ] **Step 4: Run discovery tests to verify they pass**

Run:

```bash
uv run pytest tests/unit/test_curator_engine.py -q
```

Expected: PASS, including all pre-existing single-stage tests.

- [ ] **Step 5: Commit discovery behavior**

```bash
git add src/insightcast/engines/curator_engine.py tests/unit/test_curator_engine.py
git commit -m "feat: discover ranked video topics"
```

### Task 3: Second-Stage Candidate Prompt And Orchestration

**Files:**
- Modify: `src/insightcast/prompts/curator.py`
- Modify: `src/insightcast/engines/curator_engine.py`
- Modify: `tests/unit/test_prompts.py`
- Modify: `tests/unit/test_curator_engine.py`

- [ ] **Step 1: Write failing prompt and two-stage flow tests**

Replace the curator prompt assertions in `tests/unit/test_prompts.py` with:

```python
curator_user = curator.build_user_prompt(
    transcript=[{"start_seconds": 0, "end_seconds": 3, "text": "Hello"}],
    topics=[
        {
            "topic_id": "T1",
            "label": "Important finding",
            "summary": "Summary",
            "central_claim": "Claim",
            "importance_reason": "Reason",
            "start_seconds": 0,
            "end_seconds": 300,
            "importance_score": 0.95,
        }
    ],
    candidate_count=1,
    target_min_duration_seconds=480,
    target_max_duration_seconds=720,
    accepted_min_duration_seconds=420,
    accepted_max_duration_seconds=780,
    final_min_duration_seconds=390,
    final_max_duration_seconds=810,
    validation_feedback=None,
)
payload = json.loads(curator_user)

assert curator.PROMPT_VERSION == "curator-v3"
assert payload["topics"][0]["topic_id"] == "T1"
assert payload["selection_priority"] == [
    "importance",
    "complete_argument",
    "information_density",
    "duration_fit",
]
assert payload["require_distinct_topics"] is True
assert payload["required_arc"] == [
    "necessary_background",
    "central_claim_or_finding",
    "key_evidence_or_reasoning",
    "meaningful_conclusion",
]
assert payload["final_min_duration_seconds"] == 390
assert payload["final_max_duration_seconds"] == 810
```

Add this engine test:

```python
@pytest.mark.asyncio
async def test_curate_discovers_topics_then_selects_candidates() -> None:
    topics = TopicDiscoveryResponse(
        topics=[
            topic("T1", 0, 300, 0.95),
            topic("T2", 300, 600, 0.90),
        ]
    )
    candidates = CuratorResponse(candidates=[output("A", 0, 600)])
    client = FakeStructuredClient([topics, candidates])

    result = await CuratorEngine(client=client, model="gpt-curator").curate(
        transcript=transcript(),
        candidate_count=1,
        min_duration_minutes=8,
        max_duration_minutes=12,
    )

    assert len(client.calls) == 2
    assert client.calls[0]["response_model"] is TopicDiscoveryResponse
    assert client.calls[1]["response_model"] is CuratorResponse
    second_prompt = str(client.calls[1]["user_prompt"])
    assert '"topic_id": "T1"' in second_prompt
    assert '"topic_id": "T2"' in second_prompt
    assert result.prompt_version == "topic-discovery-v1+curator-v3"
```

Update pre-existing tests that call `curate()` so each fake response list starts
with a valid topic response. Tests aimed only at candidate behavior may instead call
`select_candidates(topics=valid_topics, ...)` directly to keep their assertions
focused and avoid unrelated discovery calls.

- [ ] **Step 2: Run focused tests to verify they fail**

Run:

```bash
uv run pytest \
  tests/unit/test_prompts.py \
  tests/unit/test_curator_engine.py::test_curate_discovers_topics_then_selects_candidates \
  -q
```

Expected: FAIL because the curator prompt does not accept topics or final duration
bounds and `curate()` still performs one model call.

- [ ] **Step 3: Implement the second-stage prompt**

Update `src/insightcast/prompts/curator.py`:

```python
PROMPT_VERSION = "curator-v3"
SYSTEM_PROMPT = """You are the candidate-boundary stage of a knowledge-video curator.
Use the ranked topic pool to select the most important distinct knowledge units. Return
continuous transcript ranges that preserve necessary background, the central claim or
finding, key evidence or reasoning, and a meaningful conclusion. Remove greetings,
sponsorships, repetition, and tangents when they are not required for comprehension.
Return only the requested structured output."""


def build_user_prompt(
    *,
    transcript: Sequence[Mapping[str, Any]],
    topics: Sequence[Mapping[str, Any]],
    candidate_count: int,
    target_min_duration_seconds: float,
    target_max_duration_seconds: float,
    accepted_min_duration_seconds: float,
    accepted_max_duration_seconds: float,
    final_min_duration_seconds: float,
    final_max_duration_seconds: float,
    validation_feedback: str | None,
) -> str:
    payload = {
        "candidate_count": candidate_count,
        "topics": list(topics),
        "selection_priority": [
            "importance",
            "complete_argument",
            "information_density",
            "duration_fit",
        ],
        "require_distinct_topics": True,
        "required_arc": [
            "necessary_background",
            "central_claim_or_finding",
            "key_evidence_or_reasoning",
            "meaningful_conclusion",
        ],
        "target_min_duration_seconds": target_min_duration_seconds,
        "target_max_duration_seconds": target_max_duration_seconds,
        "accepted_min_duration_seconds": accepted_min_duration_seconds,
        "accepted_max_duration_seconds": accepted_max_duration_seconds,
        "final_min_duration_seconds": final_min_duration_seconds,
        "final_max_duration_seconds": final_max_duration_seconds,
        "times_are_approximate": True,
        "duration_instruction": (
            "Aim for the target range. Use the accepted range to preserve a complete "
            "argument. The final range is only for complete transcript-segment alignment. "
            "Do not add low-value material merely to reach a duration."
        ),
        "transcript": list(transcript),
        "validation_feedback": validation_feedback,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)
```

- [ ] **Step 4: Split engine selection and preserve the wrapper**

In `src/insightcast/engines/curator_engine.py`, add:

```python
FINAL_DURATION_SEGMENT_TOLERANCE_SECONDS = 30
```

Move the current candidate request/retry body from `curate()` into:

```python
async def select_candidates(
    self,
    *,
    transcript: Transcript,
    topics: TopicDiscoveryResponse,
    candidate_count: int,
    min_duration_minutes: float,
    max_duration_minutes: float,
) -> CurationResult:
```

Compute:

```python
target_min_duration_seconds = min_duration_minutes * 60
target_max_duration_seconds = max_duration_minutes * 60
accepted_min_duration_seconds = max(
    0,
    target_min_duration_seconds - ACCEPTED_DURATION_TOLERANCE_SECONDS,
)
accepted_max_duration_seconds = (
    target_max_duration_seconds + ACCEPTED_DURATION_TOLERANCE_SECONDS
)
final_min_duration_seconds = max(
    0,
    accepted_min_duration_seconds - FINAL_DURATION_SEGMENT_TOLERANCE_SECONDS,
)
final_max_duration_seconds = (
    accepted_max_duration_seconds + FINAL_DURATION_SEGMENT_TOLERANCE_SECONDS
)
```

Pass serialized `topics.topics` and all duration ranges to the prompt. Return:

```python
return CurationResult(
    candidates=[
        Candidate(**candidate.model_dump()) for candidate in normalized_candidates
    ],
    model=self.model,
    prompt_version=(
        f"{topic_discovery.PROMPT_VERSION}+{curator.PROMPT_VERSION}"
    ),
)
```

Rebuild `curate()` as the compatibility wrapper:

```python
async def curate(
    self,
    *,
    transcript: Transcript,
    candidate_count: int,
    min_duration_minutes: float,
    max_duration_minutes: float,
) -> CurationResult:
    topics = await self.discover_topics(
        transcript=transcript,
        candidate_count=candidate_count,
    )
    return await self.select_candidates(
        transcript=transcript,
        topics=topics,
        candidate_count=candidate_count,
        min_duration_minutes=min_duration_minutes,
        max_duration_minutes=max_duration_minutes,
    )
```

Do not add topic fields to `Candidate`, manifests, API models, or persisted JSON.

- [ ] **Step 5: Run curator and prompt tests**

Run:

```bash
uv run pytest tests/unit/test_prompts.py tests/unit/test_curator_engine.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit two-stage orchestration**

```bash
git add \
  src/insightcast/prompts/curator.py \
  src/insightcast/engines/curator_engine.py \
  tests/unit/test_prompts.py \
  tests/unit/test_curator_engine.py
git commit -m "feat: select candidates from ranked topics"
```

### Task 4: Three-Tier Duration Normalization

**Files:**
- Modify: `src/insightcast/engines/curator_engine.py`
- Modify: `tests/unit/test_curator_engine.py`

- [ ] **Step 1: Add failing final-tolerance tests**

Add cases to the existing normalization parameterization:

```python
(
    [(0, 390), (390, 810)],
    (0, 390),
    (0, 390),
),
(
    [(0, 420), (420, 810)],
    (420, 810),
    (420, 810),
),
```

These prove exact 6:30 and 13:30 complete-segment windows are accepted. Add a
failure test for a 389-second indivisible segment and an 811-second indivisible
segment:

```python
@pytest.mark.asyncio
@pytest.mark.parametrize("bounds", [((0, 389),), ((0, 811),)])
async def test_curator_rejects_windows_outside_final_duration(
    bounds: tuple[tuple[float, float], ...],
) -> None:
    topics = TopicDiscoveryResponse(
        topics=[topic("T1", 0, bounds[-1][1], 0.95), topic("T2", 0, bounds[-1][1], 0.90)]
    )
    client = FakeStructuredClient(
        [
            CuratorResponse(candidates=[output("A", 0, bounds[-1][1])]),
            CuratorResponse(candidates=[output("A", 0, bounds[-1][1])]),
        ]
    )

    with pytest.raises(InsightCastError) as exc_info:
        await CuratorEngine(client=client, model="gpt-curator").select_candidates(
            transcript=segmented_transcript(*bounds),
            topics=topics,
            candidate_count=1,
            min_duration_minutes=8,
            max_duration_minutes=12,
        )

    assert exc_info.value.error_code == ErrorCode.INVALID_LLM_OUTPUT
    assert "final range 390-810" in str(exc_info.value.details["validation_feedback"])
```

- [ ] **Step 2: Run the duration tests to verify they fail**

Run:

```bash
uv run pytest tests/unit/test_curator_engine.py -q
```

Expected: the 390-second and 810-second windows fail under the current 420-780 hard
range, and feedback does not mention the final range.

- [ ] **Step 3: Extend normalization to target, accepted, and final ranges**

Update `_normalize_candidates`, `_normalize_candidate`, and `_validate_candidates`
to accept:

```python
final_min_duration_seconds: float
final_max_duration_seconds: float
```

Keep this preference order inside `_normalize_candidate`:

1. Return immediately for a complete-segment window in the target range.
2. Save a window in the generally accepted range as the preferred fallback.
3. Save a window in the final range only when no accepted fallback exists.
4. Expand or contract while remaining within the final maximum.
5. Return the accepted fallback first, then the final fallback.
6. Return `None` when no complete-segment window lies in the final range.

Represent the fallbacks explicitly:

```python
accepted_fallback: tuple[int, int] | None = None
final_fallback: tuple[int, int] | None = None
```

Validation must use the final range:

```python
if not final_min_duration_seconds <= duration <= final_max_duration_seconds:
    errors.append(
        f"candidate {candidate.candidate_id} actual duration {duration} seconds; "
        f"target range {target_min_duration_seconds}-"
        f"{target_max_duration_seconds} seconds; accepted range "
        f"{accepted_min_duration_seconds}-{accepted_max_duration_seconds} seconds; "
        f"final range {final_min_duration_seconds}-{final_max_duration_seconds} seconds"
    )
```

- [ ] **Step 4: Run all curator tests**

Run:

```bash
uv run pytest tests/unit/test_curator_engine.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit duration policy**

```bash
git add src/insightcast/engines/curator_engine.py tests/unit/test_curator_engine.py
git commit -m "feat: allow bounded segment duration overflow"
```

### Task 5: Separate Pipeline Stages And Operation Logs

**Files:**
- Modify: `src/insightcast/services/job_service.py`
- Modify: `tests/service/test_job_service.py`

- [ ] **Step 1: Write failing service-stage tests**

Replace `FakeCurator` with a split-stage fake:

```python
from insightcast.engines.curator_engine import (
    CurationResult,
    TopicDiscoveryOutput,
    TopicDiscoveryResponse,
)


def discovered_topic(
    topic_id: str,
    start: float,
    end: float,
    score: float,
) -> TopicDiscoveryOutput:
    return TopicDiscoveryOutput(
        topic_id=topic_id,
        label=f"Topic {topic_id}",
        summary=f"Summary {topic_id}",
        central_claim=f"Claim {topic_id}",
        importance_reason=f"Reason {topic_id}",
        start_seconds=start,
        end_seconds=end,
        importance_score=score,
    )


class FakeCurator:
    def __init__(self) -> None:
        self.discovery_calls = 0
        self.selection_calls = 0

    @property
    def calls(self) -> int:
        return self.discovery_calls + self.selection_calls

    async def discover_topics(self, **_kwargs: object) -> TopicDiscoveryResponse:
        self.discovery_calls += 1
        return TopicDiscoveryResponse(
            topics=[
                discovered_topic("T1", 0, 600, 0.95),
                discovered_topic("T2", 600, 1200, 0.90),
                discovered_topic("T3", 0, 600, 0.85),
                discovered_topic("T4", 600, 1200, 0.80),
            ]
        )

    async def select_candidates(self, **kwargs: object) -> CurationResult:
        self.selection_calls += 1
        assert isinstance(kwargs["topics"], TopicDiscoveryResponse)
        return CurationResult(
            candidates=[
                Candidate(
                    candidate_id="A",
                    start_seconds=0,
                    end_seconds=600,
                    suggested_title="A",
                    selection_reason="Complete",
                    summary="Summary A",
                ),
                Candidate(
                    candidate_id="B",
                    start_seconds=600,
                    end_seconds=1200,
                    suggested_title="B",
                    selection_reason="Complete",
                    summary="Summary B",
                ),
            ],
            model="gpt-curator",
            prompt_version="topic-discovery-v1+curator-v3",
        )
```

Update assertions that expected `curator.calls == 1` to:

```python
assert curator.discovery_calls == 1
assert curator.selection_calls == 1
```

Update the forced-analysis assertion that expected `curator.calls == 2` to:

```python
assert curator.discovery_calls == 2
assert curator.selection_calls == 2
```

Update the direct-render assertion that expected `curator.calls == 0` to:

```python
assert curator.discovery_calls == 0
assert curator.selection_calls == 0
```

Update `test_pipeline_log_records_analysis_and_render_stage_timings` so its stage
tuple contains:

```python
(
    "source_ingestion",
    "transcription",
    "topic_discovery",
    "candidate_boundary_selection",
    "candidate_clip_render",
    "metadata_generation",
)
```

- [ ] **Step 2: Run service tests to verify they fail**

Run:

```bash
uv run pytest \
  tests/service/test_job_service.py::test_pipeline_log_records_analysis_and_render_stage_timings \
  tests/service/test_job_service.py::test_analysis_reuses_normalized_url_unless_forced \
  -q
```

Expected: FAIL because `JobService` still calls `curate()` and logs
`candidate_curation`.

- [ ] **Step 3: Split the service pipeline calls**

In `JobService._process_analysis`, replace the single curation stage with:

```python
self._set_status(job, JobStatus.CURATING, "Ranking important video topics.")
topics = await self._run_stage(
    job,
    "topic_discovery",
    lambda: self.curator_engine.discover_topics(
        transcript=transcript,
        candidate_count=candidate_count,
    ),
)
self._set_status(job, JobStatus.CURATING, "Selecting complete candidate ranges.")
result = await self._run_stage(
    job,
    "candidate_boundary_selection",
    lambda: self.curator_engine.select_candidates(
        transcript=transcript,
        topics=topics,
        candidate_count=candidate_count,
        min_duration_minutes=minimum,
        max_duration_minutes=maximum,
    ),
)
```

Keep all persistence after `result` unchanged. Do not write the topic pool to
`candidates.json`, candidate directories, manifests, or API responses.

Replace `FailingCurator` with:

```python
class FailingCurator:
    async def discover_topics(self, **_kwargs: object) -> TopicDiscoveryResponse:
        raise InsightCastError(
            ErrorCode.INSUFFICIENT_CANDIDATES,
            "Not enough topics.",
            stage="topic_discovery",
        )

    async def select_candidates(self, **_kwargs: object) -> CurationResult:
        raise AssertionError("selection must not run after discovery fails")
```

- [ ] **Step 4: Run service tests**

Run:

```bash
uv run pytest tests/service/test_job_service.py -q
```

Expected: PASS. The operation log includes both new curation stages and no
`candidate_curation` entry.

- [ ] **Step 5: Commit service integration**

```bash
git add src/insightcast/services/job_service.py tests/service/test_job_service.py
git commit -m "feat: log two-stage candidate curation"
```

### Task 6: Knowledge-News Metadata Prompt

**Files:**
- Modify: `src/insightcast/prompts/metadata.py`
- Modify: `tests/unit/test_prompts.py`
- Modify: `tests/unit/test_publish_engine.py`

- [ ] **Step 1: Write failing metadata prompt assertions**

In `tests/unit/test_prompts.py`, add:

```python
def test_metadata_prompt_uses_grounded_knowledge_news_framing() -> None:
    prompt = metadata.build_user_prompt(
        source_title="Foreign source",
        summary="A supported central finding",
        transcript_excerpt="Evidence and conclusion",
    )
    payload = json.loads(prompt)
    system = metadata.SYSTEM_PROMPT.lower()

    assert metadata.PROMPT_VERSION == "metadata-v2"
    assert "traditional chinese" in system
    assert "knowledge-news" in system
    assert "translated highlight" in system
    assert "original video" in system
    assert "unsupported" in system
    assert payload["title_strategy"] == [
        "what_happened_was_found_or_is_argued",
        "central_conclusion_or_consequence",
        "why_it_deserves_attention",
    ]
    assert payload["description_strategy"] == [
        "why_the_topic_matters",
        "central_claim_and_supporting_reasoning",
        "necessary_context",
        "traditional_chinese_translated_highlight_disclosure",
        "consult_original_video_for_full_discussion",
    ]
```

In `tests/unit/test_publish_engine.py`, strengthen the existing trace assertion:

```python
assert payload["trace"]["prompt_version"] == "metadata-v2"
call_prompt = json.loads(str(client.calls[0]["user_prompt"]))
assert call_prompt["summary"] == "Candidate summary"
assert call_prompt["transcript_excerpt"] == "Transcript excerpt"
```

- [ ] **Step 2: Run metadata tests to verify they fail**

Run:

```bash
uv run pytest \
  tests/unit/test_prompts.py::test_metadata_prompt_uses_grounded_knowledge_news_framing \
  tests/unit/test_publish_engine.py \
  -q
```

Expected: FAIL because metadata remains `metadata-v1` and lacks explicit strategies.

- [ ] **Step 3: Implement the metadata prompt**

Update `src/insightcast/prompts/metadata.py`:

```python
PROMPT_VERSION = "metadata-v2"
SYSTEM_PROMPT = """Create evidence-grounded Traditional Chinese knowledge-news metadata
for a translated highlight from a foreign-language YouTube video. The title should state
what happened, was found, or is being argued, the central conclusion or consequence, and
why it deserves attention. Strong framing is allowed only when supported by the summary or
transcript. Do not invent urgency, certainty, conflict, consequences, or claims that everyone
is shocked or that the topic changes everything. The description must explain significance,
summarize reasoning with necessary context, disclose that this is a Traditional Chinese
translated highlight, and direct viewers to the original video for the complete discussion.
Return title, description, accurate tags, and a privacy status that defaults to private."""


def build_user_prompt(
    *,
    source_title: str,
    summary: str,
    transcript_excerpt: str,
) -> str:
    return json.dumps(
        {
            "source_title": source_title,
            "summary": summary,
            "transcript_excerpt": transcript_excerpt,
            "title_strategy": [
                "what_happened_was_found_or_is_argued",
                "central_conclusion_or_consequence",
                "why_it_deserves_attention",
            ],
            "description_strategy": [
                "why_the_topic_matters",
                "central_claim_and_supporting_reasoning",
                "necessary_context",
                "traditional_chinese_translated_highlight_disclosure",
                "consult_original_video_for_full_discussion",
            ],
            "tag_strategy": (
                "Use only people, organizations, subjects, and concepts supported "
                "by the selected segment."
            ),
        },
        ensure_ascii=False,
        indent=2,
    )
```

No `PublishEngine`, output schema, privacy behavior, or render-flow change is
required.

- [ ] **Step 4: Run metadata tests**

Run:

```bash
uv run pytest tests/unit/test_prompts.py tests/unit/test_publish_engine.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit metadata behavior**

```bash
git add \
  src/insightcast/prompts/metadata.py \
  tests/unit/test_prompts.py \
  tests/unit/test_publish_engine.py
git commit -m "feat: generate knowledge-news metadata"
```

### Task 7: Compatibility And Full Verification

**Files:**
- Verify: `src/insightcast/prompts/topic_discovery.py`
- Verify: `src/insightcast/prompts/curator.py`
- Verify: `src/insightcast/prompts/metadata.py`
- Verify: `src/insightcast/engines/curator_engine.py`
- Verify: `src/insightcast/services/job_service.py`
- Verify: `tests/unit/test_prompts.py`
- Verify: `tests/unit/test_curator_engine.py`
- Verify: `tests/unit/test_publish_engine.py`
- Verify: `tests/service/test_job_service.py`

- [ ] **Step 1: Run focused static checks**

Run:

```bash
uv run ruff check \
  src/insightcast/prompts/topic_discovery.py \
  src/insightcast/prompts/curator.py \
  src/insightcast/prompts/metadata.py \
  src/insightcast/engines/curator_engine.py \
  src/insightcast/services/job_service.py \
  tests/unit/test_prompts.py \
  tests/unit/test_curator_engine.py \
  tests/unit/test_publish_engine.py \
  tests/service/test_job_service.py
```

Expected: PASS with no diagnostics.

- [ ] **Step 2: Run focused behavior tests**

Run:

```bash
uv run pytest \
  tests/unit/test_prompts.py \
  tests/unit/test_curator_engine.py \
  tests/unit/test_publish_engine.py \
  tests/service/test_job_service.py \
  -q
```

Expected: PASS.

- [ ] **Step 3: Run API, storage, and manifest compatibility tests**

Run:

```bash
uv run pytest \
  tests/api/test_analysis_jobs.py \
  tests/api/test_videos.py \
  tests/unit/test_video_store.py \
  tests/unit/test_manifests.py \
  tests/test_repository_contract.py \
  -q
```

Expected: PASS with no public schema or artifact-layout changes.

- [ ] **Step 4: Run the full suite**

Run:

```bash
uv run pytest -q
```

Expected: all tests pass.

- [ ] **Step 5: Verify the final diff**

Run:

```bash
git diff --check
git status --short
```

Expected: no whitespace errors and no unrelated changes. The only uncommitted files,
if any, are intentional implementation files listed by this plan.

- [ ] **Step 6: Commit any final test-only adjustments**

If verification required test corrections without production behavior changes:

```bash
git add tests
git commit -m "test: verify two-stage curation compatibility"
```

If no adjustment was required, do not create an empty commit.
