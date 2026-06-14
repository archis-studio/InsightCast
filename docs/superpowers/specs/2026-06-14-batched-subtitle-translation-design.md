# Batched Subtitle Translation Design

## Goal

Make candidate subtitle rendering reliable for long clips whose transcript contains
more subtitle items than the language model consistently returns in one structured
response.

## Problem

`LingoEngine.translate_clip` currently sends every selected transcript segment in a
single language-model request. A 12-minute candidate from video `LeUUxLRdvho`
contained 253 subtitle items. The model returned a valid structured response but
omitted the final five items, causing the strict one-to-one mapping check to fail.

The strict mapping check is correct and must remain. The request size is the unreliable
part.

## Approach

Split selected transcript segments into ordered batches of at most 40 items.

For each batch:

1. Send only that batch to the translation model.
2. Require the returned IDs to exactly equal the batch source IDs in the same order.
3. If the IDs do not match and the batch contains more than one item, split the
   batch into two ordered halves and retry each half recursively.
4. If a single-item batch still does not match, fail the render.
5. Require each translation to contain readable text.
6. Append the validated translations to the accumulated result.

After all batches succeed, construct subtitle items using the existing clipping,
relative timing, and text validation behavior.

No concurrent requests are required. Sequential batches avoid model rate bursts and
preserve deterministic ordering. Adaptive splitting handles models that truncate even
a 40-item structured response without restarting already validated batches.

## Failure Behavior

If a batch returns missing, extra, duplicated, or reordered IDs, recursively split and
retry it. Raise the existing `SUBTITLE_GENERATION_FAILED` error only when a single-item
batch still cannot map correctly.

Diagnostics will include:

- zero-based top-level batch index;
- recursive batch path such as `[1, 0]`;
- source IDs for the failed batch;
- translation IDs returned for the failed batch.

The renderer will continue writing a failed render manifest through the existing job
service behavior. Missing translations will not be replaced with English text and a
partial subtitle file will not be published.

## Compatibility

- No API schema changes.
- No domain model changes.
- No changes to SRT, ASS, MP4, metadata, or render directory formats.
- Short clips requiring one batch retain the current request and validation behavior.
- The translation prompt version remains unchanged because the prompt contract does
  not change; only request partitioning changes.

## Testing

Unit tests will cover:

- a short clip translated in one request;
- more than 40 selected segments split into ordered batches;
- exact preservation of source order across batch boundaries;
- a 40-item response missing trailing items is retried as two ordered halves;
- successful recursive retries preserve source order;
- a single-item mismatch produces `SUBTITLE_GENERATION_FAILED`;
- terminal failure diagnostics identify the top-level batch, recursive path, and
  mismatched IDs;
- existing clipping, timing, and readable-text validation tests remain green.

The full repository test suite and Ruff must pass. The real candidate A render will
then be retried with `force_render=true`, and verification will require non-empty:

- `subtitles.zh-TW.srt`;
- `subtitles.bilingual.ass`;
- `video.mp4`;
- `youtube-metadata.json`.
