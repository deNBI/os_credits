from __future__ import annotations

from asyncio import Lock
from asyncio import Queue
from asyncio import TimeoutError
from asyncio import create_task
from asyncio import gather
from asyncio import wait_for
from collections import defaultdict
from datetime import datetime
from logging.config import dictConfig
from pathlib import Path
from pprint import pformat
from typing import Optional

from aiohttp import BasicAuth
from aiohttp import ClientSession
from aiohttp import web
from aiohttp_jinja2 import setup
from aiohttp_swagger import setup_swagger
from jinja2 import FileSystemLoader
from prometheus_async import aio

from os_credits.credits.tasks import worker
from os_credits.exceptions import MissingInfluxDatabase
from os_credits.influx.client import InfluxDBClient
from os_credits.log import internal_logger
from os_credits.perun.requests import client_session
from os_credits.prometheus_metrics import projects_processed_counter
from os_credits.prometheus_metrics import tasks_queued_gauge
from os_credits.views import application_stats, delete_stuff
from os_credits.views import costs_per_hour
from os_credits.views import credits_history
from os_credits.views import credits_history_api
from os_credits.views import get_metrics
from os_credits.views import influxdb_write
from os_credits.views import ping
from os_credits.views import update_logging_config

APP_ROOT = Path(__file__).parent


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


async def create_client_session(app: web.Application) -> None:
    client_session.set(
        ClientSession(
            auth=BasicAuth(
                app["config"]["OS_CREDITS_PERUN_LOGIN"],
                app["config"]["OS_CREDITS_PERUN_PASSWORD"],
            )
        )
    )


async def setup_prometheus_metrics(app: web.Application) -> None:
    tasks_queued_gauge.set_function(lambda: app["task_queue"].qsize())


async def close_client_sessions(app: web.Application) -> None:
    try:
        await client_session.get().close()
        await app["influx_client"].close()
    except LookupError:
        # no session: no need to close a session
        pass


def create_new_group_lock() -> Lock:
    """Creates a new instance of our :ref:`Group Locks`.

    :return: New lock
    """
    projects_processed_counter.inc()
    return Lock()


async def create_app(
    _existing_influxdb_client: Optional[InfluxDBClient] = None
) -> web.Application:
    """Entry point of the whole service.

    #. Setup the logging config to be able to log as much as possible, see
       :ref:`Logging`
    #. Connect the functions to their endpoints, see :ref:`Endpoints`
    #. Setup/Create all helpers

       #. Our :class:`~os_credits.influx.client.InfluxDBClient`, see :ref:`InfluxDB
          Interaction`
       #. Create the :ref:`Task Queue` used to process incoming measurements
       #. Setup our :ref:`Group Locks`
       #. Swagger-Endpoint, see :ref:`Swagger`
       #. Setup the jinja2 template engine used by :ref:`Credits History`

    #. Make sure that the database for our :ref:`Credits History` exists. Error out if
       it does not since we (deliberately) do not run with admin access to it.
    #. Schedule the following functions to run on start

       - :func:`create_client_sessions`
       - :func:`create_worker`
       - :func:`setup_prometheus_metrics`

    #. Schedule the following functions to run on shutdown

       - :func:`stop_worker`
       - :func:`close_client_session`

    :param _existing_influxdb_client: Only used when testing the code
    :return: Created `aiohttp <https://docs.aiohttp.org>`_ Application instance.
    """
    # imported inside the function to allow pytest to set environment variables and have
    # them applied
    from os_credits.settings import config
    from os_credits.log import DEFAULT_LOGGING_CONFIG

    dictConfig(DEFAULT_LOGGING_CONFIG)
    internal_logger.info("Applied default logging config")

    app = web.Application()
    app.add_routes(
        [
            web.get("/delete", delete_stuff),
            web.get(
                "/api/credits_history/{project_name}",
                credits_history_api,
                name="api_credits_history",
            ),
            web.get("/api/metrics", get_metrics, name="get_metrics"),
            web.post("/api/costs_per_hour", costs_per_hour, name="costs_per_hour"),
            web.get(
                "/credits_history/{project_name}",
                credits_history,
                name="credits_history",
            ),
            # not naming this route since it also used as health check by Docker
            web.get("/ping", ping, name="ping"),
            web.get("/stats", application_stats),
            # not naming this route since the endpoint is defined by InfluxDB and
            # therefore fixed
            web.post("/write", influxdb_write),
            web.post("/logconfig", update_logging_config),
            web.get("/metrics", aio.web.server_stats, name="metrics"),
            web.static("/static", APP_ROOT / "static"),
        ]
    )
    app.update(
        name="os-credits",
        influx_client=_existing_influxdb_client or InfluxDBClient(),
        task_queue=Queue(),
        group_locks=defaultdict(create_new_group_lock),
        start_time=datetime.now(),
        config=config,
    )

    if not await app["influx_client"].ensure_history_db_exists():
        raise MissingInfluxDatabase(
            f"Required database {config['CREDITS_HISTORY_DB']} does not exist inside "
            "InfluxDB. Must be created externally since this code runs without admin "
            "access."
        )

    # setup jinja2 template engine
    setup(app, loader=FileSystemLoader(str(APP_ROOT / "templates")))

    app.on_startup.append(create_client_session)
    app.on_startup.append(create_worker)
    app.on_startup.append(setup_prometheus_metrics)
    app.on_cleanup.append(stop_worker)
    app.on_cleanup.append(close_client_sessions)

    setup_swagger(app)

    internal_logger.info(
        "Registered resources: %s", pformat(list(app.router.resources()))
    )

    return app
