import asyncio

import pytest

from insightcast.services.job_service import WorkItem, WorkKind
from insightcast.services.queue_worker import QueueWorker


@pytest.mark.asyncio
async def test_queue_worker_processes_fifo_without_overlap() -> None:
    events: list[str] = []
    active = 0

    class Service:
        async def process(self, item: WorkItem) -> None:
            nonlocal active
            active += 1
            assert active == 1
            events.append(f"start-{item.job_id}")
            await asyncio.sleep(0)
            events.append(f"end-{item.job_id}")
            active -= 1

    queue: asyncio.Queue[WorkItem] = asyncio.Queue()
    await queue.put(WorkItem(kind=WorkKind.ANALYSIS, job_id="one"))
    await queue.put(WorkItem(kind=WorkKind.DIRECT_RENDER, job_id="two"))
    worker = QueueWorker(queue=queue, service=Service())
    task = asyncio.create_task(worker.run())

    await queue.join()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert events == ["start-one", "end-one", "start-two", "end-two"]

