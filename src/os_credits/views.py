"""Contains all http endpoint functionality, see :mod:`os_credits.main` for the route
setup.

The endpoint can also be explored via the *Swagger UI*, usually ``/api/doc``.
"""
import logging.config
from datetime import datetime
from decimal import Decimal
from json import JSONDecodeError
from json import loads
from traceback import format_stack
from typing import Any
from typing import Dict
from typing import Optional

from aiohttp import web
from aiohttp_jinja2 import template
from os_credits.auth import auth_required

from os_credits.credits.base_models import Metric
from os_credits.influx.client import InfluxDBClient
from os_credits.influx.exceptions import InfluxDBError
from os_credits.log import internal_logger
from os_credits.perun.exceptions import GroupNotExistsError
from os_credits.settings import config
from os_credits.worker_helper import stop_worker, create_worker
from os_credits.perun.group import Group


@auth_required
async def delete_credits_left(request: web.Request) -> web.Response:
    internal_logger.info(f"Called: {request.rel_url}")
    influx_client: InfluxDBClient = request.app["influx_client"]
    deleted_from_projects = await influx_client.delete_credits_left_measurements()
    return web.json_response(deleted_from_projects)


@auth_required
async def delete_mb_and_vcpu_since(request: web.Request) -> web.Response:
    internal_logger.info(f"Called: {request.rel_url}")
    await stop_worker(request.app, 500)
    try:
        influx_client: InfluxDBClient = request.app["influx_client"]
        since_date = request.query["since_date"]
        datetime_format = "%Y-%m-%d %H:%M:%S"
        since_date = datetime.strptime(since_date, datetime_format)
        project_names = request.query["project_names"]
        project_names = project_names.split(",")
        since_date = int(since_date.timestamp())
    except KeyError as e:
        internal_logger.exception(f"Exception when getting request information for "
                                  f"deleting value history:\n"
                                  f"{e}")
        await create_worker(request.app)
        return web.HTTPException(text="Key Error.")
    except ValueError as e:
        internal_logger.exception(f"Exception when getting request information for "
                                  f"deleting value history:\n"
                                  f"{e}")
        await create_worker(request.app)
        return web.HTTPException(text="Value Error.")
    except Exception as e:
        internal_logger.exception(f"Exception when getting request information for "
                                  f"deleting value history:\n"
                                  f"{e}")
        await create_worker(request.app)
        return web.HTTPException(text="Exception.")

    return_list = []
    internal_logger.info(f"Trying to delete usage values for project: {project_names} "
                         f"since {since_date}.")
    for project_name in project_names:
        try:
            last_timestamps = await influx_client.delete_mb_and_vcpu_measurements(
                project_name,
                since_date
            )

            if "project_name" not in last_timestamps \
                    or "location_id" not in last_timestamps:
                internal_logger.info(f"Could not find group {project_name} in "
                                     f"influxdb!")
                return_list.append({"error": f"Could not find '{project_name}' "
                                             f"in influxdb."})
                continue
            perun_group = Group(last_timestamps["project_name"],
                                int(last_timestamps["location_id"]))
            await perun_group.connect()
            last_mb = last_timestamps.get("last_mb", None)
            if last_mb:
                perun_group.credits_timestamps.value[
                    "project_mb_usage"
                ] = datetime.fromtimestamp(last_timestamps["last_mb"]["time"])
            else:
                perun_group.credits_timestamps.value[
                    "project_mb_usage"
                ] = datetime.now()
            last_vcpu = last_timestamps.get("last_vcpu", None)
            if last_vcpu:
                perun_group.credits_timestamps.value[
                    "project_vcpu_usage"
                ] = datetime.fromtimestamp(last_timestamps["last_vcpu"]["time"])
            else:
                perun_group.credits_timestamps.value[
                    "project_vcpu_usage"
                ] = datetime.now()
            await perun_group.save()
            internal_logger.info(f"Deleted values and set timestamps for "
                                 f"'{project_name}' with {last_timestamps}.")
            return_list.append(last_timestamps)
        except GroupNotExistsError as e:
            internal_logger.warning(
                "Could not resolve group with name `%s` against perun. %r",
                project_name, e
            )
            return_list.append({"error": f"Could not find perun group "
                                         f"'{project_name}'."})
            continue
        except Exception as e:
            internal_logger.exception(f"Exception when deleting value history:\n"
                                      f"{e}")
            return_list.append({"error": f"Could not delete values for "
                                         f"'{project_name}'."})
            continue

    await create_worker(request.app)
    return web.json_response(return_list)


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
    internal_logger.info(f"Called: {_.rel_url}")
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
    internal_logger.info(f"Called: {request.rel_url}")
    datetime_format = "%Y-%m-%d %H:%M:%S"
    try:
        start_date = datetime.strptime(request.query["start_date"], datetime_format)
    except KeyError:
        start_date = datetime.fromtimestamp(0)
    except ValueError:
        raise web.HTTPBadRequest(reason="Invalid content for ``start_date``")
    try:
        end_date: Optional[datetime] = datetime.strptime(
            request.query["end_date"], datetime_format
        )
    except KeyError:
        end_date = None
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
    influx_client: InfluxDBClient = request.app["influx_client"]
    time_column = []
    credits_column = []
    metric_column = []
    result = await influx_client.query_billing_history(project_name, since=start_date)
    try:
        async for point in result:
            # entries are sorted by timestamp descending
            if end_date:
                if point.timestamp > end_date:
                    continue
            time_column.append(point.timestamp.strftime(datetime_format))
            credits_column.append(float(point.credits_used))
            metric_column.append(point.metric_friendly_name)
    except InfluxDBError:
        raise web.HTTPBadRequest(reason="Invalid project name")
    # check whether any data were retrieved
    if not credits_column:
        # let's check whether the project has history at all
        if await influx_client.project_has_history(project_name):
            raise web.HTTPNoContent(reason="Try changing *_date parameters")
        raise web.HTTPNotFound(reason="No data available for given parameters.")
    return web.json_response(
        {"timestamps": time_column, "credits": credits_column, "metrics": metric_column}
    )


@template("credits_history.html.j2")
async def credits_history(request: web.Request) -> Dict[str, Any]:
    """Shows a functional draft for visualization of a project's credits history.

    To generate test entries take a look at ``bin/generate_credits_history.py`` at the
    root of this project.
    """
    internal_logger.info(f"Called: {request.rel_url}")
    return {"project_name": request.match_info["project_name"]}


async def influxdb_write(request: web.Request) -> web.Response:
    """
    Consumes the `Line Protocol
    <https://docs.influxdata.com/influxdb/v1.7/write_protocols/line_protocol_tutorial/>`_
    of InfluxDB.

    :param request: Incoming request with one or multiple *InfluxDB Line* instances.
    ---
    description: Used by InfluxDB to post subscription updates
    tags:
      - Service
    consumes:
      - text/plain
    parameters:
      - in: body

        name: line

        description: Point in Line Protocol format (https://docs.influxdata.com/influxdb/v1.7/write_protocols/line_protocol_tutorial)
        schema:
          type: string

          example: weather,location=us-midwest temperature=82 1465839830100400200
        required: true
    responses:
      202:
        description: A corresponding task object will be created. See application log
          for further information
    """  # noqa (cannot fix long url)
    # .text() performs automatic decoding from bytes
    internal_logger.info(f"Called: {request.rel_url}")
    influxdb_lines = await request.text()
    # an unknown number of lines will be send, put them all into the queue
    for influx_line in influxdb_lines.splitlines():
        await request.app["task_queue"].put(influx_line)
        internal_logger.debug(
            "Put %s into queue (%s elements)",
            influx_line,
            request.app["task_queue"].qsize(),
        )
    # always answer 202
    return web.HTTPAccepted()


async def application_stats(request: web.Request) -> web.Response:
    """
    API-Endpoint returning current stats of the running application
    ---
    description: Allows querying the application state. Should not be public accessible.
    tags:
      - Health check
      - Monitoring
    produces:
      - application/json
    parameters:
      - in: query
        name: verbose
        type: boolean
        default: false
        description: Include extended (computationally expensive) information
    responses:
      200:
        description: Stats object
        schema:
          type: object
          required: [number_of_workers, queue_size, number_of_locks, uptime]
          properties:
            number_of_workers:
              type: integer
              description: Number of worker tasks as specified in config file
            queue_size:
              type: integer
              description: Number of tasks currently pending
            number_of_locks:
              type: integer
              description: Number of group/project locks inside the application, should
                correspond to the number of billed/groups/projects
            uptime:
              type: string
              description: Uptime, string representation of a python
                  [`timedelta`](
                  https://docs.python.org/3/library/datetime.html#timedelta-objects)
                object
            task_stacks:
              type: object
              required: [worker-n]
              properties:
                worker-n:
                  type: str
                  description: Stack of the worker task
            group_locks:
              type: object
              required: [group_name]
              properties:
                group_name:
                  type: str
                  description: State of the group/project async-lock
    """
    internal_logger.info(f"Called: {request.rel_url}")
    stats = {
        "number_of_workers": config["OS_CREDITS_WORKERS"],
        "queue_size": request.app["task_queue"].qsize(),
        "number_of_locks": len(request.app["group_locks"]),
        "uptime": str(datetime.now() - request.app["start_time"]),
    }
    if (
        "verbose" in request.query
        and request.query["verbose"]
        and request.query["verbose"] != "false"
    ):
        stats.update(
            {
                "task_stacks": {
                    name: [format_stack(stack)[0] for stack in task.get_stack()][0]
                    for name, task in request.app["task_workers"].items()
                },
                "group_locks": {
                    key: repr(lock) for key, lock in request.app["group_locks"].items()
                },
            }
        )
    return web.json_response(stats)


async def update_logging_config(request: web.Request) -> web.Response:
    """
    Possibility to update logging configuration without restart
    """
    internal_logger.info(f"Called: {request.rel_url}")
    logging_json_text = await request.text()
    try:
        logging_config = loads(logging_json_text)
    except JSONDecodeError as e:
        raise web.HTTPBadRequest(reason=str(e))
    try:
        logging.config.dictConfig(logging_config)
    except Exception as e:
        raise web.HTTPBadRequest(reason=str(e))
    return web.HTTPNoContent()


# Usage of class-based views would be nicer, unfortunately not yet supported by
# aiohttp-swagger
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
    internal_logger.info(f"Called: {_.rel_url}")
    metric_information = {
        friendly_name: metric.api_information()
        for friendly_name, metric in Metric.metrics_by_friendly_name.items()
    }
    return web.json_response(metric_information)


async def costs_per_hour(request: web.Request) -> web.Response:
    """Use for example

    .. code-block:: console

       $ curl localhost:8000/api/costs_per_hour \\
             -H "Content-Type: application/json" \\
             -d '{"cpu":16,"ram":32768}'

    Or if you have `httpie <https://github.com/jakubroztocil/httpie>`_ installed

    .. code-block:: console

       $ http -j :8000/api/costs_per_hour cpu:=16 ram:=32768
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
    internal_logger.info(f"Called: {request.rel_url}")
    try:
        machine_specs = await request.json()
    except JSONDecodeError:
        raise web.HTTPBadRequest(reason="Invalid JSON")
    returned_costs_per_hour = Decimal(0)
    for friendly_name, spec in machine_specs.items():
        try:
            spec = Decimal(spec)
            returned_costs_per_hour += Metric.metrics_by_friendly_name[
                friendly_name
            ].costs_per_hour(spec)
        except KeyError:
            raise web.HTTPNotFound(reason=f"Unknown measurement `{friendly_name}`.")
        except TypeError:
            raise web.HTTPBadRequest(
                reason=f"Parameter {friendly_name} had wrong type."
            )
    return web.json_response(
        float(returned_costs_per_hour.quantize(config["OS_CREDITS_PRECISION"]))
    )
