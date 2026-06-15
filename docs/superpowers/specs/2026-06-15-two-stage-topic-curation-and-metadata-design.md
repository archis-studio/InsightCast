# Two-Stage Topic Curation And Knowledge-News Metadata Design

## Goal

Improve Insight Cast for translated highlights from foreign long-form YouTube
videos:

1. Select the most important, independently valuable knowledge segments from the
   entire source instead of treating the transcript as evenly valuable material.
2. Preserve each selected segment's necessary context, argument, and conclusion.
3. Generate Traditional Chinese titles and descriptions with the clarity and
   significance of knowledge-oriented news coverage without unsupported
   sensationalism.

## Product Principles

Candidate selection follows this priority:

1. importance to the viewer;
2. completeness of the argument;
3. information density;
4. closeness to the requested duration.

Candidates are not intended to replace the source video or summarize every section.
They should expose distinct, high-value ideas and leave viewers able to seek the
full source for broader context.

Multiple candidates must represent different core topics. Small overlaps needed for
context are allowed, but candidates must not substantially repeat the same claim,
evidence, and conclusion.

## Two-Stage Curation

`CuratorEngine` will split semantic selection into topic discovery and candidate
boundary selection. Both stages use the full ordered transcript.

### Stage 1: Topic Discovery

The first model call identifies and ranks the video's important knowledge topics.
It does not produce final candidates.

The structured response contains a topic pool whose entries include:

- sequential topic ID;
- concise topic label;
- topic summary;
- central claim or finding;
- reason the topic matters;
- approximate start and end seconds;
- importance score.

The prompt instructs the model to:

- evaluate the full video before ranking topics;
- prefer claims, findings, explanations, consequences, and decisions with lasting
  knowledge value;
- distinguish genuinely different core topics;
- merge semantically duplicate topics;
- exclude greetings, sponsorships, repetition, anecdotes without a broader point,
  and setup that never reaches a conclusion;
- avoid ranking a topic highly merely because it is controversial or emotionally
  phrased.

The requested topic pool must be larger than the requested candidate count so the
second stage has alternatives when a highly ranked topic cannot form a coherent
candidate. The pool size remains an internal policy rather than a new API option.

### Stage 2: Candidate Boundary Selection

The second model call receives:

- the full ordered transcript;
- the ranked topic pool;
- requested candidate count;
- target, accepted, and final duration ranges;
- validation feedback when retrying.

It selects the highest-value set of distinct topics and returns the existing
candidate fields:

- candidate ID;
- start and end seconds;
- suggested title;
- selection reason;
- summary;
- optional score.

Each candidate must be a continuous range that includes:

- enough background to understand the topic;
- the central claim, finding, or explanation;
- the key evidence or reasoning;
- a meaningful conclusion or transition that completes the idea.

The model must not add low-value material only to meet a duration target. It should
remove greetings, repeated explanations, sponsorships, tangents, and unrelated
follow-up discussion when they are not necessary for comprehension.

The existing deterministic normalization remains responsible for snapping
approximate model times to complete transcript segment boundaries. Descriptive
candidate fields remain unchanged during normalization.

## Duration Policy

Duration is a preference hierarchy, not a single exact requirement:

- Target range: 8-12 minutes.
- Generally accepted range: 7-13 minutes.
- Final complete-segment boundary: 6 minutes 30 seconds to 13 minutes 30 seconds.

The model should aim for the target range. It may use the generally accepted range
when needed to preserve a complete argument. Deterministic normalization may exceed
that range by up to 30 seconds on either side when retaining a complete transcript
segment.

Candidates outside 6:30-13:30 remain invalid. This final boundary prevents an
unbounded interpretation of paragraph completeness while allowing small timestamp
and segment-length variations.

The current API inputs remain unchanged. Requested minimum and maximum durations
define the target range. The accepted range extends those requested bounds by 60
seconds, and the final range extends them by another 30 seconds. For the default
8-12 minute request, these resolve to 7-13 minutes and 6:30-13:30.

## Knowledge-News Metadata

The metadata prompt will produce Traditional Chinese metadata for a translated
foreign-video highlight.

### Title

The title should identify:

- what happened, was found, or is being argued;
- the central conclusion or consequence;
- why the subject deserves attention.

It may use the concise rhythm of a news headline, but every assertion must be
supported by the candidate summary or transcript. It must not manufacture urgency,
certainty, conflict, or consequences.

### Description

The description should:

1. open with a concise explanation of why the topic matters;
2. summarize the central claim and supporting reasoning;
3. provide enough context to avoid misleading viewers;
4. state that the video is a Traditional Chinese translated highlight from a
   foreign-language source;
5. encourage viewers to consult the original video for the complete discussion.

Tags should reflect the actual people, organizations, subject areas, and concepts in
the selected segment.

The prompt must explicitly reject unsupported phrases such as claims that everyone
is shocked, that a fact changes everything, or that viewers must act immediately.
The previous blanket instruction to avoid clickbait becomes a more precise rule:
strong framing is allowed when the transcript supports its significance, but
fabricated significance is not.

## Internal Data Contracts

Add internal structured models for topic discovery:

- `TopicDiscoveryOutput`
- `TopicDiscoveryResponse`

These models are not exposed through API responses or persisted as new public
artifacts in this change. They are passed directly from the first model call to the
second model call.

The final `Candidate`, analysis manifest, `candidates.json`, candidate directory,
render flow, and API schemas remain unchanged.

Increment both curator and metadata prompt versions. Existing analyses and rendered
metadata remain immutable and are not rewritten.

## Validation And Failure Handling

Topic discovery validates:

- the topic pool contains enough entries for the requested candidate count;
- topic IDs are sequential;
- labels, summaries, central claims, and importance reasons are non-empty;
- approximate ranges are positive and within the transcript;
- importance scores are present and bounded;
- topics are returned in descending importance order.

Candidate selection retains the existing identity, count, text, transcript-bound,
and segment-normalization checks. Duration validation uses the final 6:30-13:30
boundary, while retry feedback reports target, generally accepted, and final
ranges.

Each model stage retries once with concrete validation feedback. If topic discovery
still fails, curation returns the existing structured invalid-output or
insufficient-candidates error as appropriate. If candidate selection still fails,
the current candidate errors remain externally visible.

The system must not silently fill the result with duplicate or low-value topics
only to satisfy the requested count.

## Observability

The operation log will distinguish:

- topic discovery;
- candidate boundary selection;
- candidate normalization and validation.

Logs may include model name, prompt version, attempt number, counts, topic IDs,
candidate IDs, durations, and validation messages. They must not include full
prompts, full transcripts, credentials, or raw provider payloads.

## Testing

Prompt tests will verify that:

- topic discovery evaluates the entire video and ranks distinct important topics;
- candidate selection receives the ranked topic pool;
- candidate instructions prioritize importance, completeness, information density,
  and then duration;
- metadata requests knowledge-news framing, translated-highlight disclosure, source
  consultation, and evidence-grounded language;
- prompt versions are incremented.

Curator engine tests will verify:

- topic discovery runs before candidate selection;
- the second call receives the first call's ranked topics;
- the topic pool is larger than the requested candidate count;
- invalid discovery output retries with detailed feedback;
- invalid candidate output retries without repeating discovery unnecessarily when
  the valid discovered topic pool can be reused;
- final candidates retain distinct topic intent;
- segment normalization targets 8-12 minutes, accepts 7-13 minutes when needed, and
  permits complete-segment bounds only within 6:30-13:30;
- candidates outside the final range fail;
- final candidate persistence and API responses remain compatible.

Publish engine tests will verify the updated metadata prompt inputs and prompt
version trace. Existing service, API, storage, render, and repository contract tests
must remain green.

## Success Criteria

- Candidate A represents the most important coherent knowledge segment found across
  the full transcript, not merely an early or easily bounded section.
- Additional candidates cover different important core topics.
- Each selected clip can be understood independently and ends after its key idea is
  meaningfully resolved.
- Default candidates aim for 8-12 minutes, normally remain within 7-13 minutes, and
  never exceed 6:30-13:30 after complete-segment normalization.
- Generated titles and descriptions communicate significance in natural
  Traditional Chinese without claims unsupported by the selected content.
- The analysis API, persisted candidate schema, and render workflow remain backward
  compatible.
- Analysis does not queue rendering unless explicitly requested.
