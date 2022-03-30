import asyncio
from asyncio import Queue, Lock
from collections import defaultdict
from datetime import datetime
from logging.config import dictConfig
from pathlib import Path
from pprint import pformat
from aiohttp import web
from aiohttp_jinja2 import setup
from aiohttp_swagger import setup_swagger
from jinja2 import FileSystemLoader
from src.os_credits.worker_helper import create_consumer_worker, stop_consumer_worker, create_producer
from src.os_credits.db_client.client import TimescaleDBManager
from src.os_credits.log import internal_logger
from src.os_credits.views import ping, credits_history_api, costs_per_hour, get_metrics, get_current_credits

APP_ROOT = Path(__file__).parent


async def create_app() -> web.Application:
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

    :return: Created `aiohttp <https://docs.aiohttp.org>`_ Application instance.
    """
    # imported inside the function to allow pytest to set environment variables and have
    # them applied
    from src.os_credits.settings import config
    from src.os_credits.log import DEFAULT_LOGGING_CONFIG

    dictConfig(DEFAULT_LOGGING_CONFIG)
    internal_logger.info("Applied default logging config")

    app = web.Application()
    app.add_routes(
        [
            web.get(
                "/api/credits_history/{project_name}",
                credits_history_api,
                name="api_credits_history",
            ),
            web.get("/api/metrics", get_metrics, name="get_metrics"),
            web.post("/api/costs_per_hour", costs_per_hour, name="costs_per_hour"),
            web.get(
                "/api/current_credits/{project_name}",
                get_current_credits,
                name="get_current_credits",
            ),
            # web.get(
            #     "/credits_history/{project_name}",
            #     credits_history,
            #     name="credits_history",
            # ),
            # not naming this route since it also used as health check by Docker
            web.get("/ping", ping, name="ping"),
            # web.get("/stats", application_stats),
            # web.post("/logconfig", update_logging_config),
            # web.get("/metrics", aio.web.server_stats, name="metrics"),
            # web.static("/static", APP_ROOT / "static"),
        ]
    )
    app.update(
        name="os-credits",
        database_client=TimescaleDBManager(),
        task_queue=Queue(),
        group_locks=defaultdict(Lock),
        start_time=datetime.now(),
        config=config,
    )
    connected = False
    while not connected:
        try:
            await app["database_client"].initialize()
            internal_logger.info("Got a database connection and database is initialized.")
            connected = True
        except ConnectionRefusedError:
            internal_logger.info("Got no database connection, sleeping for 10 and retrying.")
            await asyncio.sleep(10)
    # setup jinja2 template engine
    setup(app, loader=FileSystemLoader(str(APP_ROOT / "templates")))

    if not config["ENDPOINTS_ONLY"]:
        app.on_startup.append(create_consumer_worker)
        app.on_startup.append(create_producer)
        app.on_cleanup.append(stop_consumer_worker)

    setup_swagger(app)

    internal_logger.info(
        "Registered resources: %s", pformat(list(app.router.resources()))
    )

    return app
