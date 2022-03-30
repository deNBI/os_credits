"""Contains all http endpoint functionality, see :mod:`os_credits.main` for the route
setup.

The endpoint can also be explored via the *Swagger UI*, usually ``/api/doc``.
"""
from decimal import Decimal
from json import JSONDecodeError
from datetime import datetime
from typing import Optional

from aiohttp import web

from os_credits.db_client.client import TimescaleDBManager
from os_credits.log import views_logger
from os_credits.settings import config

_DEFINITELY_PAST = datetime.min
_DEFINITELY_END = datetime.max


async def ping(_: web.Request) -> web.Response:
    """
    Simple ping endpoint to be able to determine whether the application is up and
    running.

    :return: Response with the text body ``Pong``.

    ---
    description: This end-point allow to test that service is up.
    tags:
    - Health check
    produces:
    - text/plain
    responses:
        200:
            description: successful operation. Return "Pong" text
        405:
            description: invalid HTTP Method
    """
    views_logger.info(f"Called: {_.rel_url}")
    return web.Response(text="Pong")


async def credits_history_api(request: web.Request) -> web.Response:
    """Endpoint for the website provided by :func:`credits_history` to retrieve its
    data, the credits history of a given project.

    ---
    description: >
      Provides the history of credits of the given project. The return format is
      currently optimized against ``c3.js`` which is used by the internal visualization.
      The first entry of every response array is a string followed by the data. The
      ``metrics`` array contains the ``friendly_name`` of the metric responsible for
      this billing. The ``timestamps`` array contains the timestamps of the measurements
      which caused the billing. To generate test entries take a look at
      ``bin/generate_credits_history.py`` at the root of this project.  Timestamps are
      formatted ``%Y-%m-%d %H:%M:%S`` and sorted descending.
    tags:
      - Service
    produces:
    - application/json
    parameters:
      - name: project_name
        in: path
        type: string
        description: Name of the project
      - name: start_date
        in: query
        type: string
        format: date
        description: Start date of the credits data, format ``%Y-%m-%d %H:%M:%S``
      - name: end_date
        in: query
        type: string
        format: date
        description: End date of the credits data, format ``%Y-%m-%d %H:%M:%S``
    responses:
      200:
        description: Credits history
        schema:
          type: object
          required: [timestamps, credits, metrics]
          properties:
            timestamps:
              type: array
              items:
                type: str
            credits:
              type: array
              items:
                type: float
            metrics:
              type: array
              items:
                type: str
      200:
        description: Response with requested data.
      204:
        description: Project does have credits history but not for given parameters.
      400:
        description: Bad value of one or more parameters.
      404:
        description: Could not find any history data.
    """
    views_logger.info(f"Called: {request.rel_url}")
    datetime_format = "%Y-%m-%d %H:%M:%S"
    try:
        start_date = datetime.strptime(request.query["start_date"], datetime_format)
    except KeyError:
        start_date = _DEFINITELY_PAST
    except ValueError:
        raise web.HTTPBadRequest(reason="Invalid content for ``start_date``")
    try:
        end_date: Optional[datetime] = datetime.strptime(
            request.query["end_date"], datetime_format
        )
    except KeyError:
        end_date = _DEFINITELY_END
    except ValueError:
        raise web.HTTPBadRequest(reason="Invalid content for ``end_date``")
    if end_date and end_date <= start_date:
        raise web.HTTPBadRequest(
            reason="``start_date`` must be older than ``end_date``."
        )
    try:
        project_name = request.match_info["project_name"]
        # Swagger UI sends '{project_name}' if none is specified -.-'
        if project_name == "{project_name}" or not project_name.strip():
            raise KeyError
    except KeyError:
        raise web.HTTPBadRequest(reason="No non-empty ``project_name`` provided")
    database_client: TimescaleDBManager = request.app["database_client"]
    time_column = []
    credits_column = []
    metric_column = []
    async with database_client.async_session() as session:
        result = await database_client.get_credits_history(project_name, since=start_date, end=end_date, session=session)
    for credits_entry in result:
        time_column.append(credits_entry[0].time.strftime(datetime_format))
        credits_column.append(float(credits_entry[0].used_credits))
        metric_column.append(credits_entry[0].by_metric)
    # check whether any data were retrieved
    if not credits_column:
        # let's check whether the project has history at all
        raise web.HTTPNotFound(reason="No data available for given parameters.")
    return web.json_response(
        {"timestamps": time_column, "credits": credits_column, "metrics": metric_column}
    )

#
#
# @template("credits_history.html.j2")
# async def credits_history(request: web.Request) -> Dict[str, Any]:
#     """Shows a functional draft for visualization of a project's credits history.
#
#     To generate test entries take a look at ``bin/generate_credits_history.py`` at the
#     root of this project.
#     """
#     internal_logger.info(f"Called: {request.rel_url}")
#     return {"project_name": request.match_info["project_name"]}
#
#
# async def influxdb_write(request: web.Request) -> web.Response:
#     """
#     Consumes the `Line Protocol
#     <https://docs.influxdata.com/influxdb/v1.7/write_protocols/line_protocol_tutorial/>`_
#     of InfluxDB.
#
#     :param request: Incoming request with one or multiple *InfluxDB Line* instances.
#     ---
#     description: Used by InfluxDB to post subscription updates
#     tags:
#       - Service
#     consumes:
#       - text/plain
#     parameters:
#       - in: body
#
#         name: line
#
#         description: Point in Line Protocol format (https://docs.influxdata.com/influxdb/v1.7/write_protocols/line_protocol_tutorial)
#         schema:
#           type: string
#
#           example: weather,location=us-midwest temperature=82 1465839830100400200
#         required: true
#     responses:
#       202:
#         description: A corresponding task object will be created. See application log
#           for further information
#     """  # noqa (cannot fix long url)
#     # .text() performs automatic decoding from bytes
#     internal_logger.info(f"Called: {request.rel_url}")
#     influxdb_lines = await request.text()
#     # an unknown number of lines will be send, put them all into the queue
#     for influx_line in influxdb_lines.splitlines():
#         await request.app["task_queue"].put(influx_line)
#         internal_logger.debug(
#             "Put %s into queue (%s elements)",
#             influx_line,
#             request.app["task_queue"].qsize(),
#         )
#     # always answer 202
#     return web.HTTPAccepted()
#
#
# async def application_stats(request: web.Request) -> web.Response:
#     """
#     API-Endpoint returning current stats of the running application
#     ---
#     description: Allows querying the application state. Should not be public accessible.
#     tags:
#       - Health check
#       - Monitoring
#     produces:
#       - application/json
#     parameters:
#       - in: query
#         name: verbose
#         type: boolean
#         default: false
#         description: Include extended (computationally expensive) information
#     responses:
#       200:
#         description: Stats object
#         schema:
#           type: object
#           required: [number_of_workers, queue_size, number_of_locks, uptime]
#           properties:
#             number_of_workers:
#               type: integer
#               description: Number of worker tasks as specified in config file
#             queue_size:
#               type: integer
#               description: Number of tasks currently pending
#             number_of_locks:
#               type: integer
#               description: Number of group/project locks inside the application, should
#                 correspond to the number of billed/groups/projects
#             uptime:
#               type: string
#               description: Uptime, string representation of a python
#                   [`timedelta`](
#                   https://docs.python.org/3/library/datetime.html#timedelta-objects)
#                 object
#             task_stacks:
#               type: object
#               required: [worker-n]
#               properties:
#                 worker-n:
#                   type: str
#                   description: Stack of the worker task
#             group_locks:
#               type: object
#               required: [group_name]
#               properties:
#                 group_name:
#                   type: str
#                   description: State of the group/project async-lock
#     """
#     internal_logger.info(f"Called: {request.rel_url}")
#     stats = {
#         "number_of_workers": config["OS_CREDITS_WORKERS"],
#         "queue_size": request.app["task_queue"].qsize(),
#         "number_of_locks": len(request.app["group_locks"]),
#         "uptime": str(datetime.now() - request.app["start_time"]),
#     }
#     if (
#         "verbose" in request.query
#         and request.query["verbose"]
#         and request.query["verbose"] != "false"
#     ):
#         stats.update(
#             {
#                 "task_stacks": {
#                     name: [format_stack(stack)[0] for stack in task.get_stack()][0]
#                     for name, task in request.app["task_workers"].items()
#                 },
#                 "group_locks": {
#                     key: repr(lock) for key, lock in request.app["group_locks"].items()
#                 },
#             }
#         )
#     return web.json_response(stats)


async def get_metrics(_: web.Request) -> web.Response:
    """
    Returns a JSON object describing the currently supported metrics and their per-hour
    costs.
    ---
    description: Get type and description of currently needed/supported measurements.
      Also describes the structure of the corresponding POST API to calculate the per
      hour-usage of a given machine constellation.
    tags:
      - Service
    produces:
      - application/json
    responses:
      200:
        description: Information object
        schema:
          type: object
          required: [metrics]
          properties:
            metrics:
              type: object
              required: [description, type, metric_name, friendly_name]
              properties:
                description:
                  type: str
                  description: Description of the measurement
                type:
                  type: str
                  description: Type information
                metric_name:
                  type: str
                  description: Name/Identifier of the metric inside prometheus and
                      InfluxDB
                friendly_name:
                  type: str
                  description: Human readable name of the metric.
    """
    views_logger.info(f"Called: {_.rel_url}")
    metric_information = {
        metric_name: float(cost)
        for metric_name, cost in config["METRICS_TO_BILL"].items()
    }
    return web.json_response(metric_information)


async def costs_per_hour(request: web.Request) -> web.Response:
    """Use for example

    .. code-block:: console

       $ curl localhost:8000/api/costs_per_hour \\
             -H "Content-Type: application/json" \\
             -d '{"project_vcpu_usage":16,"project_mb_usage":32768}'

    ---
    description: Given the submitted specs of one or multiple machines combined
      calculate the expected costs per hour. See the ``GET /api/metrics`` to retrieve
      information about the supported specs. Since the input is dynamic and we are not
      using swagger models you have to query the API from the command line. See official
      documentation of the function for example calls.
    tags:
      - Service
    consumes:
      - application/json
    produces:
      - application/json
    responses:
      200:
        description: Costs per hour
        schema:
          type: float
    """
    views_logger.info(f"Called: {request.rel_url}")
    try:
        machine_specs = await request.json()
    except JSONDecodeError:
        raise web.HTTPBadRequest(reason="Invalid JSON")
    returned_costs_per_hour = Decimal(0)
    for metric_name, spec in machine_specs.items():
        try:
            cost = Decimal(config["METRICS_TO_BILL"][metric_name])
            spec = Decimal(spec)
            returned_costs_per_hour += (spec * cost).quantize(config["OS_CREDITS_PRECISION"])
        except KeyError:
            raise web.HTTPNotFound(reason=f"Unknown measurement `{metric_name}`.")
        except TypeError:
            raise web.HTTPBadRequest(
                reason=f"Parameter {metric_name} had wrong type."
            )
    return web.json_response(
        float(returned_costs_per_hour.quantize(config["OS_CREDITS_PRECISION"]))
    )


async def get_current_credits(request: web.Request) -> web.Response:
    try:
        project_name = request.match_info["project_name"]
        # Swagger UI sends '{project_name}' if none is specified -.-'
        if project_name == "{project_name}" or not project_name.strip():
            raise KeyError
    except KeyError:
        raise web.HTTPBadRequest(reason="No non-empty ``project_name`` provided")
    database_client: TimescaleDBManager = request.app["database_client"]
    async with database_client.async_session() as session:
        project = await database_client.get_project(project_name, session)
    if not project:
        raise web.HTTPNotFound(reason=f"No credits found for {project_name}.")
    return web.json_response(
        {"current_credits": float(project.used_credits)}
    )
