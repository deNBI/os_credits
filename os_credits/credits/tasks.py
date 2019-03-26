"""
Performs the actual calculations concerning usage and the resulting credit 'billing'
"""
from __future__ import annotations

from asyncio import Lock
from typing import Dict

from aiohttp.web import Application

from os_credits.exceptions import DenbiCreditsCurrentError, GroupNotExistsError
from os_credits.influxdb import InfluxDBPoint
from os_credits.log import TASK_ID, task_logger
from os_credits.perun.groupsManager import Group
from os_credits.settings import config

from .measurements import Measurement, calculate_credits


def unique_identifier(influx_line: str) -> str:
    # insert leading zeros if less numbers than 12 but don't use more
    return format(abs(hash(influx_line)), ">012")[:12]


async def worker(name: str, app: Application) -> None:
    group_locks = app["group_locks"]
    task_queue = app["task_queue"]
    while True:
        influx_line: str = await task_queue.get()
        task_id = unique_identifier(influx_line)
        TASK_ID.set(task_id)
        task_logger.debug("Worker %s starting task `%s`", name, task_id)

        try:
            await process_influx_line(influx_line, app, group_locks)
            task_logger.debug(
                "Worker %s finished task `%s` successfully", name, task_id
            )
        except Exception:
            task_logger.exception("%s threw an exception:", name)
        finally:
            task_queue.task_done()


async def process_influx_line(
    influx_line: str, app: Application, group_locks: Dict[str, Lock]
) -> None:
    try:
        influx_point = InfluxDBPoint.from_influx_line(influx_line)
    except (KeyError, ValueError):
        task_logger.exception(
            "Could not convert influx line %s to InfluxDBPoint. Appending stacktrace",
            influx_line,
        )
        return
    perun_group = Group(influx_point.project_name, influx_point.location_id)
    if "OS_CREDITS_PROJECT_WHITELIST" in config:
        if perun_group.name not in config["OS_CREDITS_PROJECT_WHITELIST"]:
            task_logger.info(
                "Group `%s` is not part of given whitelist (%s). Ignoring measurement",
                perun_group.name,
                config["OS_CREDITS_PROJECT_WHITELIST"],
            )
            return
    try:
        measurement = Measurement.create_measurement(
            influx_point.measurement_name, influx_point.value, influx_point.timestamp
        )
    except ValueError:
        task_logger.info(
            "Ignoring %s since the measurement is not needed/billable", influx_point
        )
        return
    task_logger.info(
        "Processing Measurement `%s` - Group `%s`", measurement, perun_group
    )
    task_logger.debug("Awaiting async lock for Group %s", perun_group.name)
    async with group_locks[perun_group.name]:
        task_logger.debug("Acquired async lock for Group %s", perun_group.name)
        await update_credits(perun_group, measurement, app)


async def update_credits(
    group: Group, current_measurement: Measurement, app: Application
) -> None:
    try:
        await group.connect()
    except GroupNotExistsError as e:
        task_logger.warning(
            "Could not resolve group with name `%s` against perun. %r", group.name, e
        )
        return
    if group.credits_current.value is None:
        # let's check whether any measurement timestamps are present, if so we are
        # having a problem since this means that this group has been processed before!
        if group.credits_timestamps.value:
            raise DenbiCreditsCurrentError(
                f"Group {group.name} has been billed before but is missing "
                "`credits_current` now. "
                "Did someone modify the values by hand? Aborting"
            )
        else:
            task_logger.info(
                "Group %s does not have `credits_current` and hasn't been billed before "
                "copying the value of `credits_granted`",
                group,
            )
            group.credits_current.value = group.credits_granted.value
    try:
        last_measurement_timestamp = group.credits_timestamps.value[
            current_measurement.prometheus_name
        ]
    except KeyError:
        task_logger.info(
            "Group %s has no timestamp of most recent measurement of %s. "
            "Setting it to the timestamp of the current measurement.",
            group,
            current_measurement.prometheus_name,
        )
        # set timestamp of current measurement so we can start billing the group once
        # the next measurements are submitted
        group.credits_timestamps.value[
            current_measurement.prometheus_name
        ] = current_measurement.timestamp
        await group.save()
        return
    task_logger.debug(
        "Last time credits were billed: %s",
        group.credits_timestamps.value[current_measurement.prometheus_name],
    )

    if current_measurement.timestamp < last_measurement_timestamp:
        task_logger.warning(
            "Current measurement is OLDER than the last measurement. HOW? Ignoring"
        )
        return

    project_measurements = await app["influx_client"].entries_by_project_since(
        project_name=group.name,
        since=last_measurement_timestamp,
        measurement_name=current_measurement.prometheus_name,
    )
    try:
        last_measurement_value = project_measurements.loc[
            last_measurement_timestamp
        ].value
    except KeyError:
        oldest_measurement_timestamp = project_measurements.head(
            1
        ).index.to_pydatetime()[0]

        group.credits_timestamps.value[
            current_measurement.prometheus_name
        ] = oldest_measurement_timestamp
        task_logger.warning(
            "InfluxDB does not contains usage values for Group %s for measurement %s "
            "at timestamp %s, which means that the period between the last measurement "
            "and now cannot be used for credit billing. Setting the timestamp to the "
            "oldest measurement between now and the last time measurements were billed "
            "inside InfluxDB (%s)",
            group,
            current_measurement.prometheus_name,
            last_measurement_timestamp,
            oldest_measurement_timestamp,
        )
        await group.save()
        return

    last_measurement = Measurement.create_measurement(
        prometheus_name=current_measurement.prometheus_name,
        timestamp=last_measurement_timestamp,
        value=last_measurement_value,
    )

    credits_to_bill = calculate_credits(current_measurement, last_measurement)
    group.credits_timestamps.value[
        current_measurement.prometheus_name
    ] = current_measurement.timestamp

    group.credits_current.value -= credits_to_bill
    task_logger.info(
        "Credits: %f - %f = %f",
        group.credits_current.value + credits_to_bill,
        credits_to_bill,
        group.credits_current.value,
    )
    await group.save()
