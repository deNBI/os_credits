import asyncio
from asyncio import CancelledError, shield, Queue, Lock
from typing import cast, Dict

from aiohttp.web import Application
from sqlalchemy.ext.asyncio import AsyncSession

from src.os_credits.db_client.client import TimescaleDBManager
from src.os_credits.log import producer_logger, task_logger, TASK_ID
from src.os_credits.settings import config


def unique_identifier(project_name: str) -> str:
    """Hashes the passed project name and returns an unique ID.

    Used to uniquely identify all log messages related to one specific project name.
    Needed since multiple ones are processed in parallel to the logs are scattered. Used
    as :ref:`Logging`.

    :param project_name: String to hash
    :return: Unique ID consisting of 12 numbers
    """
    # insert leading zeros if less numbers than 12 but don't use more
    return format(abs(hash(project_name)), ">012")[:12]


async def put_projects_into_queue(app: Application) -> None:
    """
    Worker putting labels from promscale into queue.
    :param app: Application.
    :return: None
    """
    db_client: TimescaleDBManager = app["database_client"]
    queue: Queue = app["task_queue"]
    while True:
        q_size = queue.qsize()
        if q_size > 0:
            producer_logger.warning(f"There are still {q_size} objects in queue. Sleeping for 60s and then retrying.")
            await asyncio.sleep(60)
            continue
        producer_logger.info("Producer starting to put all projects into queue.")
        async with db_client.async_session() as session:
            labels_with_project_names_as_value = await db_client.fetch_all_labels_for_project_name_key(session)
            await db_client.check_measurement_classes(session)
            for label in labels_with_project_names_as_value:
                producer_logger.debug("Processing label {0}.".format(label))
                if config["OS_CREDITS_PROJECT_WHITELIST"] and label[0].value not in config["OS_CREDITS_PROJECT_WHITELIST"]:
                    producer_logger.debug(
                        "Project {0} is not in the given whitelist. Not putting into queue.".format(label.value)
                    )
                else:
                    await queue.put(label)
                producer_logger.debug("Queue size now {0}.".format(queue.qsize()))
        producer_logger.info("Producer sleeping now for 5 minutes.")
        await asyncio.sleep(5 * 60)


async def consumer_worker(name: str, app: Application) -> None:
    task_queue: Queue = app["task_queue"]
    db_client: TimescaleDBManager = app["database_client"]
    group_locks = cast(Dict[str, Lock], app["group_locks"])
    while True:
        async with db_client.async_session() as session:
            try:
                label = await task_queue.get()
                label = label[0]
            except CancelledError:
                task_logger.info(f"Worker {name} was cancelled when waiting for new item.")
                raise
            try:
                task_id = unique_identifier(label.value)
                TASK_ID.set(task_id)
                task_logger.info("Worker {0} starting task `{1}` for project {2}".format(
                    name, task_id, label.value
                ))
                # do not cancel a running task
                await shield(process_project(label, db_client, session, group_locks))
                task_logger.info(
                    "Worker {0} finished task `{1}` successfully".format(
                        name, task_id
                    )
                )
            # necessary since the tasks must continue working despite any exceptions that
            # occurred
            except CancelledError:
                raise
            except Exception as e:
                task_logger.exception(
                    f"Worker {name} exited task with unhandled exception: {e}, stacktrace "
                    f"attached",
                )
            finally:
                task_queue.task_done()


async def process_project(
    label, db_client: TimescaleDBManager, session: AsyncSession, group_locks: Dict[str, Lock]
):
    task_logger.debug("Going to process {0}".format(label.value))
    async with group_locks[label.value]:
        await compute_credits_by_label(label, session, db_client)
        await session.commit()


async def compute_credits_by_label(
    label, session: AsyncSession, db_client: TimescaleDBManager
):
    task_logger.debug(f"Updating credits for {label.value}.")

    project = await db_client.get_project_by_label(label, session)
    if project is None:
        project = await db_client.create_project_by_label(label, session)
    else:
        project = project[0]
        session.add(project)
    await db_client.get_granted_credits_for_project(project, session)
    half_credits = project.granted_credits / 2
    measurements = await db_client.get_measurement_classes()
    for measurement_name, measurement_class in measurements.items():
        last_metric_credits = await db_client.get_latest_metric_credits(
            project=project, metric=measurement_name, session=session
        )
        if last_metric_credits is None:
            last_metric_credits = await db_client.initialize_first_metric_credits_entry(
                project=project, metric=measurement_name, session=session
            )
        else:
            last_metric_credits = last_metric_credits[0]

        last_credits_entry = await db_client.get_latest_credits(
            project_name=project.project_name, session=session
        )
        if last_credits_entry is None:
            last_credits_entry = await db_client.initialize_first_credits_entry(
                project=project, metric_credits=last_metric_credits, session=session
            )

        measurments_since = await db_client.fetch_measurements_since_inclusive_last(
            project, last_metric_credits, session
        )
        if len(measurments_since) < 2:
            task_logger.debug("No new metric rows for {0} for {1}.".format(
                project, measurement_name
            ))
            continue
        metric_num = 0
        while metric_num < len(measurments_since) - 1:
            current_measurement = measurments_since[metric_num]
            next_measurement = measurments_since[metric_num + 1]
            task_logger.debug(
                "Processing current: {0} and next: {1} for {2} for {3}.".format(
                    current_measurement, next_measurement, project, measurement_name
                )
            )
            credits_value: float = await db_client.calculate_credits_with_two_measurements(
                current_measurement, next_measurement, measurement_name
            )
            if credits_value == 0.0:
                metric_num += 1
                continue
            task_logger.debug(
                "Got a credits value of {0} for {1} and {2}.".format(
                    credits_value, project, measurement_name
                )
            )
            last_metric_credits = await db_client.add_metric_credits(
                project=project, last_metric_credits=last_metric_credits, credits_value=credits_value, measurement=next_measurement,
                session=session
            )
            last_credits_entry = await db_client.add_credits(
                project=project, metric_credits=last_metric_credits, session=session,
                credits_value=(last_credits_entry.used_credits + credits_value)
            )
            if project.granted_credits > 0 and last_credits_entry.used_credits >= half_credits and not project.half_limit_reached_send:
                await db_client.inform_half_limit_reached(last_credits_entry, project, session)
            task_logger.debug("Last metric now: {0}".format(last_metric_credits))
            task_logger.debug("Last credits now: {0}".format(last_credits_entry))
            metric_num += 1
        task_logger.debug("Updated credits for {0} with {1}.".format(project, measurement_name))
