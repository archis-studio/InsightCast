import asyncio
from typing import Any

from insightcast.services.job_service import WorkItem


class QueueWorker:
    def __init__(self, *, queue: asyncio.Queue[WorkItem], service: Any) -> None:
        self.queue = queue
        self.service = service

    async def run(self) -> None:
        while True:
            item = await self.queue.get()
            try:
                await self.service.process(item)
            finally:
                self.queue.task_done()

