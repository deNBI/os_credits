"""This module contains our :class:`InfluxDBClient` which is a subclass of the one
provided by the ``aioinflux`` package.

Ours does not rewrite any functionality, it only contains additional functions to ease
writing and querying our custom data models which are all based on
:class:`~os_credits.influx.model.InfluxDBPoint`.

When querying :class:`~collections.abc.AsyncGenerator` are used whenever possible, to
prevent excessive loading data when only e.g. the last 5 entries are needed.
"""
from __future__ import annotations

from datetime import datetime
from itertools import chain
from textwrap import shorten
from typing import AsyncGenerator
from typing import Dict
from typing import Iterable
from typing import List
from typing import Optional
from typing import Type
from typing import Union
from warnings import catch_warnings
from warnings import filterwarnings

from os_credits.credits.base_models import UsageMeasurement
from os_credits.credits.models import BillingHistory
from os_credits.log import influxdb_logger
from os_credits.settings import config

from .exceptions import InfluxDBError
from .model import PT

# Suppress warning about missing support for DataFrames since pandas is not installed
# Would increase the size of the application too much
with catch_warnings():
    filterwarnings("ignore", category=UserWarning)
    from aioinflux import iterpoints
    from aioinflux.client import InfluxDBClient as _InfluxDBClient
    from aioinflux.client import InfluxDBError as _InfluxDBError


INFLUX_QUERY_DATE_FORMAT = "%Y-%m-%d %H:%M:%S.%f"

_DEFINITELY_PAST = datetime.fromtimestamp(0)


class InfluxDBClient(_InfluxDBClient):
    def __init__(self, loop=None) -> None:
        super().__init__(
            host=config["INFLUXDB_HOST"],
            port=config["INFLUXDB_PORT"],
            username=config["INFLUXDB_USER"],
            password=config["INFLUXDB_USER_PASSWORD"],
            database=config["INFLUXDB_DB"],
            loop=loop,
            output="json",
        )

    async def delete_points(self, project_name_to_delete, since_date):
        delete_mb_template = f"delete from project_mb_usage where project_name='{project_name_to_delete}' and time > {since_date};"
        delete_vcpu_template = f"delete from project_vcpu_usage where project_name='{project_name_to_delete}' and time > {since_date};"
        delete_billing_template = f"delete from {project_name_to_delete} where time > {since_date};"
        await self.query(delete_mb_template)
        await self.query(delete_vcpu_template)
        await self.query(delete_billing_template, db=config["CREDITS_HISTORY_DB"])

    async def ensure_history_db_exists(self) -> bool:
        """Checks whether the required database for credits history exists.

        The name of the database is stored inside the
        :class:`~os_credits.config.Config`.

        :return: Whether the database exists
        """
        r = await self.show_databases()
        return config["CREDITS_HISTORY_DB"] in chain.from_iterable(
            r["results"][0]["series"][0]["values"]
        )

    @staticmethod
    def sanitize_parameter(parameter: str) -> str:
        """Sanitizes the provided parameter to prevent SQL Injection when querying with
        user provided content.

        :param parameter: Content to sanitize
        :return: Sanitized string
        """
        # TODO: probably way too restrictive/wrong, but works for now, better fail than
        # SQL injection
        critical_chars = {"'", '"', "\\", ";", " ", ","}
        sanitized_param_chars: List[str] = []
        for char in parameter:
            if char in critical_chars:
                sanitized_param_chars.append(f"\\{char}")
            else:
                sanitized_param_chars.append(char)
        sanitized_param = "".join(sanitized_param_chars)
        if sanitized_param != parameter:
            influxdb_logger.debug("Sanitized %s to %s", parameter, sanitized_param)
        return sanitized_param

    async def query_points(
        self,
        measurement: str,
        point_class: Type[PT],
        db: str,
        query_constraints: Optional[List[str]] = None,
    ) -> AsyncGenerator[PT, None]:
        """Asynchronously yields all queried points which in turn are streamed in chunks
        from the InfluxDB, where the points are sorted by their timestamp descending.

        :param measurement: Which table to run the query against.
        :param point_class: Subclass of ``InfluxDBPoint`` whose ``from_iterpoint``
            method will be used to deserialize the returned points.
        :param db: Which database to run the query against.
        :param query_constraints: WHERE-constraints to add to the query, will be AND-ed
            if more than one is given.
        :return: Instances of ``point_class`` ordered by their timestamp descending.
        """
        query_template = """\
        SELECT *
        FROM {measurement}
        {constraints}
        ORDER BY time DESC
        """
        constraints = ""
        if query_constraints and len(query_constraints) > 0:
            constraints = f"WHERE {' AND '.join(query_constraints)}"
        query = query_template.format(constraints=constraints, measurement=measurement)
        influxdb_logger.debug(
            "Sending query `%s` to InfluxDB",
            shorten(query.replace("\n", ""), len(query)),
        )
        result = await self.query(query, chunked=True, db=db)
        try:
            # If an error occurs it is raised here due to ``chunked=True``
            async for chunk in result:
                for point in iterpoints(chunk, point_class.from_iterpoint):
                    yield point
        except _InfluxDBError as e:
            influxdb_logger.exception("Exception when querying InfluxDB")
            raise InfluxDBError(*e.args)

    async def query_points_since(
        self,
        measurement: str,
        point_class: Type[PT],
        db: str,
        since: datetime = _DEFINITELY_PAST,
        query_constraints: Optional[List[str]] = None,
    ) -> AsyncGenerator[PT, None]:
        """Wrapper around ``query_points`` to emulate ``WHERE time >= since`` constraint
        of InfluxDB.

        Necessary since it returns wrong results (for me), fixing definitely
        appreciated.

        :param since: Only return Points whose timestamp is >=, i.e. which are not older
        """
        async for point in self.query_points(
            measurement=measurement,
            point_class=point_class,
            db=db,
            query_constraints=query_constraints,
        ):
            if point.timestamp >= since:
                yield point
            else:
                return

    async def previous_measurements(
        self, measurement: UsageMeasurement, since: datetime = _DEFINITELY_PAST
    ) -> Dict[datetime, UsageMeasurement]:
        """Return previous measurements for the same project and metric as the provided
        measurement.

        :param measurement: Must be initialized since its project and metric values are
            required.
        :param since: Passed through to ``query_points_since``.
        :return: Dictionary of measurements accessible by their timestamp which are
            sorted descending.
        """
        sanitized_project_name = InfluxDBClient.sanitize_parameter(
            measurement.project_name
        )
        previous_measurements = self.query_points_since(
            measurement=measurement.metric.name,
            point_class=type(measurement),
            db=config["INFLUXDB_DB"],
            since=since,
            query_constraints=[f"project_name = '{sanitized_project_name}'"],
        )
        return {point.timestamp: point async for point in previous_measurements}

    async def write_billing_history(
        self, point: Union[BillingHistory, Iterable[BillingHistory]]
    ) -> None:
        await self.write(point, db=config["CREDITS_HISTORY_DB"])

    async def project_has_history(self, project_name: str) -> bool:
        """Checks whether a project has any history stored or not

        :param project_name: Project name to check
        :return: History present or not
        """
        r = await self.show_series(db=config["CREDITS_HISTORY_DB"])
        for project_measurement in chain.from_iterable(
            r["results"][0]["series"][0]["values"]
        ):
            if project_measurement.startswith(project_name):
                return True

        return False

    async def query_billing_history(
        self, project_name: str, since: datetime = _DEFINITELY_PAST
    ) -> AsyncGenerator[BillingHistory, None]:
        """Return the billing history of the specified project.

        :param project_name: Project whose history should be queried.
        :param since: Passed through to ``query_points_since``.
        :return: Asynchronously yielded instances of ``BillingHistory`` sorted by their
            timestamp descending.
        """
        sanitized_project_name = InfluxDBClient.sanitize_parameter(project_name)
        return self.query_points_since(
            measurement=sanitized_project_name,
            db=config["CREDITS_HISTORY_DB"],
            point_class=BillingHistory,
            since=since,
        )
