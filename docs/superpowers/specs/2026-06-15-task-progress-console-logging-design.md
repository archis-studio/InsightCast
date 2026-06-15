# Task Progress Console Logging Design

## Goal

Expose concise background-task progress in the `cast_api` console so operators can
confirm that analysis and rendering are advancing without repeatedly polling job
endpoints.

The console output must remain sparse enough that Uvicorn access logs are easy to
read. Existing per-operation file logs remain the detailed diagnostic record.

## Current Behavior

`JobService` already records job status changes, stage timings, cache decisions, and
exceptions through `insightcast.job.<job_id>` loggers. Those loggers write to the
operation log and set `propagate = False`, so none of that progress appears in the
`cast_api` console.

Uvicorn continues to own server startup, error, and access logging.

## Design

Add a dedicated module logger for concise task progress, separate from each job's
file logger. It will emit through the logging configuration installed by Uvicorn.
The existing job logger will keep `propagate = False`.

The task logger will emit one structured, human-readable line for:

- each persisted job status change;
- each stage start;
- each stage completion, including elapsed seconds;
- each stage failure, including elapsed seconds;
- each terminal analysis, candidate-render, or direct-render failure.

It will not emit:

- polling or heartbeat messages;
- API request details already covered by Uvicorn access logs;
- raw LLM or API payloads;
- artifact path inventories;
- cache decisions;
- tracebacks already recorded in the operation log.

## Event Format

Messages use stable `key=value` fields so they are readable in a terminal and easy
to search:

```text
task job_id=<id> type=<job-type> status=<status> message="<message>"
task job_id=<id> type=<job-type> stage=<stage> event=started
task job_id=<id> type=<job-type> stage=<stage> event=completed elapsed_seconds=<seconds>
task job_id=<id> type=<job-type> stage=<stage> event=failed elapsed_seconds=<seconds>
task job_id=<id> type=<job-type> event=failed error_code=<code> stage=<stage>
```

The logger name will be `insightcast.task`. Status and successful stage events use
`INFO`. Stage and terminal failures use `ERROR`.

Messages are produced with logging placeholders rather than preformatted f-strings.
No custom JSON formatter or logging dependency is introduced.

## Integration Points

`JobService._touch()` will emit the status event after updating the job timestamp.
This covers analysis, candidate render, and direct render state transitions through
their existing shared path.

`JobService._run_stage()` will emit start, completion, and failure events alongside
the existing detailed job-file events. The same measured duration will be used for
both destinations.

Terminal exception handling will emit one concise failure event containing the
structured error code and stage. The operation logger remains responsible for the
traceback.

Initial `QUEUED` creation events will also be emitted through the shared task logging
helper, even though job creation currently writes directly through the file logger.
This gives operators a visible beginning for every task.

Candidate render batch completion is represented by the parent analysis job status
event. The line includes the analysis job ID; render IDs and candidate IDs are not
added unless needed to distinguish concurrent batches in a future design.

## Logging Configuration

The feature relies on the standard-library `logging` module and Uvicorn's existing
console configuration. `cast_api` will not install another root handler or call
`logging.basicConfig()`, avoiding duplicate server and access logs.

When code is exercised outside Uvicorn, such as unit tests or direct library use,
the logger remains a normal module logger and can be captured or configured by the
caller.

## Error Handling

Logging is observational and must not change task behavior. No logging call will
catch, replace, or translate pipeline exceptions.

The concise console failure line omits traceback details. Operators can use the
included job ID to find the existing operation log for diagnosis.

## Tests

Focused tests will use logging capture to verify:

- queued and subsequent status changes emit one concise task event;
- stage start and completion events include job type, stage, and elapsed time;
- stage failures use `ERROR` and include elapsed time;
- terminal structured failures include error code and stage;
- the existing job file logger still has one file handler and does not propagate;
- no heartbeat or polling behavior is introduced.

Existing service tests will continue to verify detailed operation-log stage records.
The full relevant unit and service suites will run after implementation.

## Non-Goals

- streaming task status over HTTP, SSE, or WebSockets;
- changing API response schemas or persisted job state;
- configurable log formats or verbosity controls;
- periodic progress percentages or heartbeats;
- replacing operation logs with console logs.
