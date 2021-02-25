from os_credits.credits.tasks import worker
from aiohttp import web
from asyncio import TimeoutError
from asyncio import create_task
from asyncio import gather
from asyncio import wait_for
from os_credits.log import internal_logger


async def create_worker(app: web.Application) -> None:
    """Creates :ref:`Task Workers` to process items put into the :ref:`Task Queue`.

    The amount of them can configured via ``OS_CREDITS_WORKERS``, see :ref:`Settings`.
    """
    app["task_workers"] = {
        f"worker-{i}": create_task(worker(f"worker-{i}", app))
        for i in range(app["config"]["OS_CREDITS_WORKERS"])
    }
    internal_logger.info("Created %d workers", len(app["task_workers"]))


async def stop_worker(app: web.Application, queue_timeout: int = 120) -> None:
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
            "workers now.",
            queue_timeout,
        )
    for task in app["task_workers"].values():
        task.cancel()
    await gather(*app["task_workers"].values(), return_exceptions=True)
