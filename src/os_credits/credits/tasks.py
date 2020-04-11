"""
Performs the actual calculations concerning usage and the resulting credit 'billing'
"""
from __future__ import annotations

from asyncio import CancelledError
from asyncio import Lock
from asyncio import Queue
from asyncio import shield
from decimal import Decimal
from typing import Dict
from typing import cast

from aiohttp.web import Application

from os_credits.influx.client import InfluxDBClient
from os_credits.log import TASK_ID
from os_credits.log import task_logger
from os_credits.notifications import EmailNotificationBase
from os_credits.notifications import HalfOfCreditsLeft
from os_credits.notifications import send_notification
from os_credits.perun.exceptions import DenbiCreditsUsedMissing
from os_credits.perun.exceptions import GroupNotExistsError
from os_credits.perun.group import Group
from os_credits.prometheus_metrics import worker_exceptions_counter
from os_credits.settings import config

from .base_models import Credits
from .base_models import UsageMeasurement
from .billing import calculate_credits
from .models import BillingHistory
from .models import measurement_by_name


def unique_identifier(influx_line: str) -> str:
    """Hashes the passed Influx Line and returns an unique ID.

    Used to uniquely identify all log messages related to one specific Influx Line.
    Needed since multiple ones are processed in parallel to the logs are scattered. Used
    as :ref:`Logging`.

    :param influx_line: String to hash
    :return: Unique ID consisting of 12 numbers
    """
    # insert leading zeros if less numbers than 12 but don't use more
    return format(abs(hash(influx_line)), ">012")[:12]


async def worker(name: str, app: Application) -> None:
    """Worker task to process Influx Lines put into the :ref:`Task Queue` by the
    ``/write`` endpoint(:func:`~os_credits.views.influxdb_write`).

    Runs inside a ``while True`` loop, and blocks until it retrieves an item from the
    :ref:`Task Queue`.

    #. Calls :func:`unique_identifier` to generate a unique ID for the Influx Line
    #. Shields :func:`process_influx_line` by wrapping it in :func:`~asyncio.shield`.
       Must be shielded since the attributes of group objects are retrieved and saved
       with two separate calls to *Perun*. The task **must not** be cancelled between
       the two ``save`` calls.
    #. In case exceptions raised when processing the item or when sending the
       notification log it properly.
    #. Finally, in every case, signal to the queue that the task has been processed.


    :param name: Name of this worker used for logging
    :param app: Application instance holding the helper class instances
    """
    group_locks = cast(Dict[str, Lock], app["group_locks"])
    task_queue = cast(Queue, app["task_queue"])
    while True:
        try:
            influx_line: str = await task_queue.get()
        except CancelledError:
            task_logger.info("Worker %s was cancelled when waiting for new item.", name)
            raise
        try:
            task_id = unique_identifier(influx_line)
            TASK_ID.set(task_id)
            task_logger.debug("Worker %s starting task `%s`", name, task_id)

            # do not cancel a running task
            await shield(process_influx_line(influx_line, app, group_locks))
            task_logger.debug(
                "Worker %s finished task `%s` successfully", name, task_id
            )
        # necessary since the tasks must continue working despite any exceptions that
        # occurred
        except CancelledError:
            raise
        except Exception as e:
            worker_exceptions_counter.inc()
            task_logger.exception(
                "Worker %s exited task with unhandled exception: %s, stacktrace "
                "attached",
                name,
                e,
            )
        finally:
            task_queue.task_done()


async def process_influx_line(
    influx_line: str, app: Application, group_locks: Dict[str, Lock]
) -> None:
    """Performs all preliminary task before actually billing a Group/Project.

    #. Determine whether the passed item/str/Influx Line is billable/needed
    #. Deserialize it into a :class:`~os_credits.models.UsageMeasurement` by calling
       :func:`~os_credits.credits.models.measurement_by_name`, see :ref:`Metrics and
       Measurements`.
    #. Create a :class:`~os_credits.perun.group.Group` object, see :ref:`Perun`.
    #. If a project whitelist is set in :ref:`Settings`, see whether the group is part
       of it
    #. Calls :func:`update_credits` once the correct :ref:`lock <Group Locks>` could be
       acquired. Catch every notification, see :ref:`Notifications`, and send it.

    :param influx_line: String/Influx Line to process
    :param app: Application object holding our helper class instances
    :param group_locks: Dictionary with :ref:`Group Locks`
    """
    task_logger.info("Processing Influx Line `%s`", influx_line)
    # we want to end this task as quickly as possible if the InfluxDB Point is not
    # needed
    try:
        measurement_class = measurement_by_name(influx_line)
    except ValueError:
        task_logger.debug("Ignoring since the measurement is not needed/billable")
        return
    try:
        measurement = measurement_class.from_lineprotocol(influx_line)
    except (KeyError, ValueError):
        task_logger.exception(
            "Could not convert influx line %s to UsageMeasurement. Appending "
            "stacktrace",
            influx_line,
        )
        return
    perun_group = Group(measurement.project_name, measurement.location_id)
    if (
        config["OS_CREDITS_PROJECT_WHITELIST"] is not None
        and perun_group.name not in config["OS_CREDITS_PROJECT_WHITELIST"]
    ):
        task_logger.info(
            "Group `%s` is not part of given whitelist (%s). Ignoring measurement",
            perun_group.name,
            config["OS_CREDITS_PROJECT_WHITELIST"],
        )
        return
    task_logger.info(
        "Processing UsageMeasurement `%s` - Group `%s`", measurement, perun_group
    )
    task_logger.debug("Awaiting async lock for Group %s", perun_group.name)
    try:
        # since group_locks is a defaultdict a new Lock is automatically created if
        # necessary
        async with group_locks[perun_group.name]:
            task_logger.debug("Acquired async lock for Group %s", perun_group.name)
            await update_credits(perun_group, measurement, app)
    except EmailNotificationBase as notification:
        task_logger.info("Sending notification %s", notification)
        await send_notification(notification)


async def update_credits(
    group: Group, current_measurement: UsageMeasurement, app: Application
) -> None:
    """Evaluates the measurement and decides what to do.

    #. Connect the :ref:`group <Groups>`
    #. If the amount of used credits is not set yet:

       #. If no timestamp of the metric of the current measurement exists this group has
          never been billed before. Initialize the used credits with 0.
       #. If a timestamp exists the group **must** have been billed before and the
          absence of ``credits_used`` is an error in which case
          :exc:`~os_credits.exceptions.DenbiCreditsUsedMissing` is raised.
    #. If the metric has not been billed before store the timestamp of the current
       measurement and send the values to *Perun*.
    #. Retrieve previous measurements of this group and metric especially the one whose
       timestamp is stored in the group. Perform additional tests to make sure that we
       can continue billing this group and metric.
    #. Call :func:`~os_credits.credits.billing.calculate_credits` to let the
       metric calculate how many credits should be billed for the current measurement.
    #. In case of a positive amount of credits to bill do so, store the timestamp of
       current measurement inside the group, create an entry for the :ref:`Credits
       History` and send the changed group attributes to *Perun*.

    When taking a look at the test coverage under ``htmlcov/tests/index.html`` this file
    should have a very high value. Whenever you add a corner case or just another simple
    if statement **write a test for it**!

     .. todo::

        Metrics should decide how to react to a value change, the current behaviour is
        tied to TotalUsageMetrics! Idea: Something like `raise ProceedWithoutBilling` in
        case of a lower value which might be related to a change of the **start**
        parameter of the *OpenStack Usage Exporter*.

    :param group: Group whose measurement is processed - Unconnected
    :param current_measurement: Current measurement to process
    :param app: Application instance holding the helper class instances
    :raises EmailNotificationBase: Subclasses of it which are actual notifications can
        be raised throughout the whole codebase.
    :raise DenbiCreditsUsedMissing: See documentation above.
    """
    try:
        await group.connect()
    except GroupNotExistsError as e:
        task_logger.warning(
            "Could not resolve group with name `%s` against perun. %r", group.name, e
        )
        return
    if group.credits_used.value is None:
        # let's check whether any measurement timestamps are present, if so we are
        # having a problem since this means that this group has been processed before!
        if group.credits_timestamps.value:
            raise DenbiCreditsUsedMissing(
                f"Group {group.name} has non-empty credits_timestamps and therefore "
                "processed before but is missing `credits_used` now. "
                "Did someone modify the values by hand? Aborting"
            )
        else:
            task_logger.info(
                "Group %s does not have `credits_used` and hasn't been billed before: "
                "Initialising with 0",
                group,
            )
            group.credits_used.value = Decimal(0)
    try:
        last_measurement_timestamp = group.credits_timestamps.value[
            current_measurement.measurement
        ]
    except KeyError:
        task_logger.info(
            "Group %s has no timestamp of most recent measurement of %s. "
            "Setting it to the timestamp of the current measurement.",
            group,
            current_measurement.measurement,
        )
        # set timestamp of current measurement so we can start billing the group once
        # the next measurements are submitted
        group.credits_timestamps.value[
            current_measurement.measurement
        ] = current_measurement.timestamp
        await group.save()
        return
    task_logger.debug(
        "Last time credits were billed: %s",
        group.credits_timestamps.value[current_measurement.measurement],
    )

    if current_measurement.timestamp <= last_measurement_timestamp:
        task_logger.warning(
            "Current measurement is not more recent than the last measurement. HOW? "
            "Ignoring"
        )
        return

    # help type checker since it can not infer the type of app['influx_client']
    # statically
    influx_client = cast(InfluxDBClient, app["influx_client"])
    project_measurements = await influx_client.previous_measurements(
        measurement=current_measurement, since=last_measurement_timestamp
    )
    try:
        last_measurement = project_measurements[last_measurement_timestamp]
    except KeyError:
        oldest_measurement_timestamp = list(project_measurements)[0]

        group.credits_timestamps.value[
            current_measurement.measurement
        ] = oldest_measurement_timestamp
        task_logger.warning(
            "InfluxDB does not contains usage values for Group %s for measurement %s "
            "at timestamp %s, which means that the period between the last measurement "
            "and now cannot be used for credit billing. Setting the timestamp to the "
            "oldest measurement between now and the last time measurements were billed "
            "inside InfluxDB (%s)",
            group,
            current_measurement.measurement,
            last_measurement_timestamp,
            oldest_measurement_timestamp,
        )
        await group.save()
        return

    # see TODO in __doc__
    if current_measurement.value == last_measurement.value:
        task_logger.info(
            "Values of this and previously billed measurement do not differ, "
            "dropping it."
        )
        return

    credits_to_bill = calculate_credits(current_measurement, last_measurement)
    group.credits_timestamps.value[
        current_measurement.measurement
    ] = current_measurement.timestamp

    previous_group_credits = group.credits_used.value
    group.credits_used.value = group.credits_used.value + credits_to_bill
    # Comparing the actual values makes sure that this case even triggers if
    # credits_to_bill is not zero but so small that its changes are dropped due to
    # rounding
    if previous_group_credits == group.credits_used.value:
        task_logger.info(
            "Measurement does not change the amount of credits left due to rounding, "
            "therefore no changes will be stored inside Perun or the InfluxDB."
        )
        return
    task_logger.info(
        "Credits billed: %f, total Usage: %s/%d",
        credits_to_bill,
        group.credits_used.value,
        group.credits_granted.value,
    )
    billing_entry = BillingHistory(
        measurement=group.name,
        timestamp=current_measurement.timestamp,
        credits_left=Credits(group.credits_granted.value - group.credits_used.value),
        metric_name=current_measurement.metric.name,
        metric_friendly_name=current_measurement.metric.friendly_name,
    )
    await influx_client.write_billing_history(billing_entry)
    await group.save()
    half_of_credits_granted = Decimal(group.credits_granted.value) / 2
    if (
        previous_group_credits <= half_of_credits_granted
        and group.credits_used.value > half_of_credits_granted
    ):
        raise HalfOfCreditsLeft(group)
