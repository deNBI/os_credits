import asyncio

from os_credits.db_client.tasks import consumer_worker, put_projects_into_queue
from aiohttp import web
from asyncio import TimeoutError
from asyncio import create_task
from asyncio import gather
from asyncio import wait_for
from .log import internal_logger


async def create_producer(app: web.Application) -> None:
    app["producer_worker"] = {
        f"producer_worker-0": create_task(put_projects_into_queue(app))
    }


async def create_consumer_worker(app: web.Application) -> None:
    """Creates :ref:`Task Workers` to process items put into the :ref:`Task Queue`.

    The amount of them can configured via ``OS_CREDITS_WORKERS``, see :ref:`Settings`.
    """
    app["consumer_workers"] = {
        f"consumer_worker-{i}": create_task(consumer_worker(f"consumer_worker-{i}", app))
        for i in range(app["config"]["OS_CREDITS_WORKERS"])
    }
    internal_logger.info("Created %d consumer workers", len(app["consumer_workers"]))


async def stop_consumer_worker(app: web.Application, queue_timeout: int = 120) -> None:
    """Tries to shutdown all :ref:`Task Workers` gracefully by first emptying the
    :ref:`Task Queue` before cancelling the workers.

    :param app: Application instance holding the worker tasks and the task queue.
    :param queue_timeout: Seconds to wait finish remaining tasks in queue before killing
        task workers.
    """
    internal_logger.info(
        "Waiting up to %d seconds to finish remaining tasks.", queue_timeout
    )
    try:
        await wait_for(app["task_queue"].join(), timeout=queue_timeout)
    except TimeoutError:
        internal_logger.warning(
            "Waited %d seconds for all remaining tasks to be processed, killing "
            "consumer workers now.",
            queue_timeout,
        )
    for task in app["consumer_workers"].values():
        task.cancel()
    await gather(*app["consumer_workers"].values(), return_exceptions=True)
