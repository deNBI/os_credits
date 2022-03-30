from datetime import datetime
from time import sleep

import aiohttp
from aiohttp import ClientConnectorError
from sqlalchemy import select, desc, create_engine, and_, asc
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, Session
from src.os_credits.settings import config
from src.os_credits.db_client.model import Base, MetricCredits, Project, Credits, PromCatalogReflected, PromMetricReflected, \
    make_measurement_class, Metric, Label, \
    BaseMeasurement
from src.os_credits.log import timescaledb_logger


class TimescaleDBManager:

    def __init__(self):
        # Create sync engine and session for table creation and promscale reflection,
        # as sqlalchemy inspector can not yet handle async connections
        _DB_URI_SYNC = "postgresql+psycopg2://{user}:{password}@{host}:{port}/{db_name}".format(
            user=config["POSTGRES_USER"],
            password=config["POSTGRES_PASSWORD"],
            host=config["POSTGRES_HOST"],
            port=config["POSTGRES_PORT"],
            db_name=config["POSTGRES_DB"]
        )
        self.sync_engine = create_engine(_DB_URI_SYNC)
        self.sync_session = sessionmaker(self.sync_engine)

        # Create async engine and sessionmaker for crud operations
        _DB_URI_ASYNC = "postgresql+asyncpg://{user}:{password}@{host}:{port}/{db_name}".format(
            user=config["POSTGRES_USER"],
            password=config["POSTGRES_PASSWORD"],
            host=config["POSTGRES_HOST"],
            port=config["POSTGRES_PORT"],
            db_name=config["POSTGRES_DB"]
        )
        self.engine = create_async_engine(_DB_URI_ASYNC)
        self.async_session = sessionmaker(self.engine, expire_on_commit=False, class_=AsyncSession, autoflush=False)
        self.client_session = aiohttp.ClientSession()

        self.measurement_classes = {}
        self.base = Base

    async def initialize(self):
        timescaledb_logger.info(f"Initializing database with sync engine.")
        # Create tables from models.py
        self.create_tables()
        # Reflect all promscale tables and init classes
        if not config["ENDPOINTS_ONLY"]:
            self.reflect_promscale_tables()
        timescaledb_logger.info(f"Initializing done. Disposing of sync engine.")
        self.sync_engine.dispose()

    #########################################################
    # init functions ########################################

    def create_tables(self):
        timescaledb_logger.info("Creating tables from base.")
        connection_available = False
        while not connection_available:
            try:
                self.base.metadata.create_all(self.sync_engine)
                connection_available = True
            except OperationalError:
                timescaledb_logger.info(f"Database not online yet. Sleeping for 10s.")
                sleep(10)

    def reflect_promscale_tables(self):
        timescaledb_logger.info("Reflecting _prom_catalog tables.")
        PromCatalogReflected.prepare(self.sync_engine)
        with self.sync_session() as session:
            self.wait_for_metric_names(session)
            self.create_prom_metric_classes(session)
            timescaledb_logger.info("Reflecting prom_metric tables.")
            PromMetricReflected.prepare(self.sync_engine)

    def wait_for_metric_names(self, session: Session):
        timescaledb_logger.info("Checking if there are any metric names.")
        metric_names_exist = False
        while not metric_names_exist:
            metric_names = self.fetch_metric_names_sync(session)
            if len(metric_names) > 0:
                metric_names_exist = True
            else:
                timescaledb_logger.info("No metric names yet. Sleeping for 10 and retrying.")
                sleep(10)

    def create_prom_metric_classes(self, session: Session):
        self.measurement_classes = {}
        metric_names = self.fetch_metric_names_sync(session)
        timescaledb_logger.info(f"Creating BaseMeasurement objects for {metric_names}.")
        for metric in metric_names:
            timescaledb_logger.info(f"Creating BaseMeasurement object for {metric}.")
            self.measurement_classes[metric[0].metric_name] = make_measurement_class(metric[0].metric_name)

    @classmethod
    def fetch_metric_names_sync(cls, session: Session):
        metric_names_result = session.execute(select(Metric))
        metric_names = [row
                        for row in metric_names_result.all()
                        if row is not None
                        and row.Metric.metric_name in config["METRICS_TO_BILL"]]
        return metric_names

    # init functions end ################################
    #####################################################

    #####################################################
    #####################################################
    # fetch promscale data ##############################

    # get metrics #######################################

    @classmethod
    async def fetch_metric_names(cls, session: AsyncSession):
        metric_names_result = await session.execute(select(Metric))
        metric_names = [row
                        for row in metric_names_result.all()
                        if row is not None
                        and row.Metric.metric_name in config["METRICS_TO_BILL"]]
        return metric_names

    # get label data ####################################

    @classmethod
    async def fetch_all_labels(cls, session: AsyncSession):
        labels_result = await session.execute(select(Label))
        return labels_result.all()

    @classmethod
    async def fetch_label_by_project_name(cls, project_name, session: AsyncSession): # noqa
        label_result = await session.execute(select(Label).where(Label.value == project_name))
        return label_result.first()

    @classmethod
    async def fetch_all_labels_for_project_name_key(cls, session: AsyncSession):
        labels_result = await session.execute(select(Label).where(Label.key == "project_name"))
        return labels_result.all()

    # get measurements ##################################

    async def check_measurement_classes(self, session: AsyncSession):
        metrics = await self.fetch_metric_names(session)
        timescaledb_logger.debug(f"Checking if {metrics} in {self.measurement_classes}.")
        for metric in metrics:
            if metric[0].metric_name not in self.measurement_classes:
                timescaledb_logger.debug(f"{metric} not found. Reflecting and disposing sync_engine.")
                self.reflect_promscale_tables()
                self.sync_engine.dispose()
                break

    async def get_measurement_classes(self):
        return self.measurement_classes

    async def fetch_all_metric_data(self, metric_name, session: AsyncSession):
        measurement_results = await session.execute(select(
            self.measurement_classes[metric_name].time,
            self.measurement_classes[metric_name].project_name_id,
            self.measurement_classes[metric_name].value
        ))
        return measurement_results.all()

    async def fetch_first_metric_data_by_project_name(self, project: Project, metric_name: str, session: AsyncSession):
        measurement_result = await session.execute(
            select(
                self.measurement_classes[metric_name].time,
                self.measurement_classes[metric_name].project_name_id,
                self.measurement_classes[metric_name].value
            ).where(
                self.measurement_classes[metric_name].project_name_id == project.project_name_label_id
            ).order_by(desc("time"))
        )
        return measurement_result.first()

    async def fetch_measurements_since_inclusive_last(
        self, project, metric_credis, session: AsyncSession
    ):
        measurement_results = await session.execute(
            select(
                self.measurement_classes[metric_credis.metric].time,
                self.measurement_classes[metric_credis.metric].project_name_id,
                self.measurement_classes[metric_credis.metric].value
            ).where(
                and_(
                    self.measurement_classes[metric_credis.metric].project_name_id == project.project_name_label_id,
                    self.measurement_classes[metric_credis.metric].time >= metric_credis.time
                )
            ).order_by(asc("time"))
        )
        return measurement_results.all()

    async def get_newest_measurement(
        self, project: Project, metric_credis: MetricCredits, session: AsyncSession
    ):
        measurement_results = await session.execute(
            select(
                self.measurement_classes[metric_credis.metric].time,
                self.measurement_classes[metric_credis.metric].project_name_id,
                self.measurement_classes[metric_credis.metric].value
            ).where(
                self.measurement_classes[metric_credis.metric].project_name_id == project.project_name_label_id
            ).order_by(asc("time"))
        )
        return measurement_results.first()

    # fetch promscale data end ##########################
    #####################################################
    #####################################################

    #####################################################
    #####################################################
    # handle own data ###################################

    # project ###########################################

    @classmethod
    async def create_project_by_label(cls, label: Label, session: AsyncSession):
        project: Project = Project(
            project_name=label.value,
            project_name_label_id=label.id
        )
        session.add(project)
        await session.commit()
        return project

    @classmethod
    async def get_project_by_label(cls, label: Label, session: AsyncSession):
        project_result = await session.execute(
            select(Project).where(Project.project_name == label.value)
        )
        return project_result.first()

    @classmethod
    async def get_project(cls, project_name: str, session: AsyncSession):
        project_result = await session.execute(
            select(Project).where(Project.project_name == project_name)
        )
        return project_result.first()

    async def get_granted_credits_for_project(self, project: Project, session):
        timeout = aiohttp.ClientTimeout(total=10, connect=5)
        params = {"project_name": project.project_name}
        headers = {"X-Api-Key": config["API_CONTACT_KEY"]}
        try:
            async with self.client_session.get(
                f"{config['API_CONTACT_BASE_URL']}/secure/granted-credits/",
                timeout=timeout,
                params=params,
                headers=headers
            ) as response:
                if response.status == 200:
                    text = await response.text()
                    project.granted_credits = float(text)
                    if project.half_limit_reached_send:
                        half_limit = project.granted_credits / 2.0
                        if project.used_credits < half_limit:
                            project.half_limit_reached_send = False
                    await session.flush()
                else:
                    timescaledb_logger.warning(f"Could not get granted credits for {project}")
        except ClientConnectorError:
            timescaledb_logger.debug(f"No connection possible to get granted credits for {project}.")
        except Exception as e:
            timescaledb_logger.exception(e)

    async def inform_half_limit_reached(self, last_credits_entry, project, session):
        timeout = aiohttp.ClientTimeout(total=10, connect=5)
        data = {
            "project_name": project.project_name,
            "granted_credits": project.granted_credits,
            "used_credits": last_credits_entry.used_credits,
            "timestamp": datetime.timestamp(last_credits_entry.time)
        }
        headers = {"X-Api-Key": config["API_CONTACT_KEY"]}
        try:
            timescaledb_logger.info(f"Sending information about half limit reached for {project} with {last_credits_entry}.")
            async with self.client_session.post(
                f"{config['MAIL_CONTACT_URL']}",
                timeout=timeout,
                data=data,
                headers=headers
            ) as response:
                if response.status == 200:
                    project.half_limit_reached_send = True
                    await session.flush()
                    timescaledb_logger.info(f"Information about half limit reached send for {project} with {last_credits_entry}.")
                else:
                    timescaledb_logger.warning(f"Could not send half limit reached mail for {project} with {last_credits_entry}")
        except ClientConnectorError:
            timescaledb_logger.debug(f"No connection possible to send half limit reached mail for {project} with {last_credits_entry}.")
        except Exception as e:
            timescaledb_logger.exception(e)

    # credits ###########################################

    @classmethod
    async def initialize_first_credits_entry(
        cls, project: Project, metric_credits: MetricCredits, session: AsyncSession
    ):
        last_credits = Credits(
            # time=metric_credits.time,
            used_credits=metric_credits.used_credits,
            granted_credits=project.granted_credits,
            project_name=project.project_name,
            by_metric=metric_credits.metric,
            metric_time=metric_credits.time
        )
        session.add(last_credits)
        await session.flush()
        return last_credits

    @classmethod
    async def add_credits(
        cls, project: Project, metric_credits: MetricCredits, session: AsyncSession, credits_value: float
    ):
        last_credits = Credits(
            # time=metric_credits.time,
            used_credits=credits_value,
            granted_credits=project.granted_credits,
            project_name=project.project_name,
            by_metric=metric_credits.metric,
            metric_time=metric_credits.time
        )
        project.used_credits = last_credits.used_credits
        session.add(last_credits)
        await session.flush()
        return last_credits

    @classmethod
    async def get_credits_history(cls, project_name: str, since, end, session: AsyncSession):
        credits_result = await session.execute(
            select(Credits).where(
                and_(
                    Credits.project_name == project_name,
                    Credits.time <= end,
                    Credits.time >= since
                )
            ).order_by(asc(Credits.time))
        )
        return credits_result.all()

    @classmethod
    async def get_latest_credits(cls, project_name: str, session: AsyncSession):
        last_credits_result = await session.execute(
            select(Credits.used_credits).where(Credits.project_name == project_name).order_by(desc(Credits.time))
        )
        return last_credits_result.first()

    # metric credits ####################################

    @classmethod
    async def get_latest_metric_credits(cls, project: Project, metric: str, session: AsyncSession):
        last_value_result = await session.execute(
            select(MetricCredits).where(
                and_(
                    MetricCredits.project_name == project.project_name,
                    MetricCredits.metric == metric
                )
            ).order_by(desc(MetricCredits.time))
        )
        return last_value_result.first()

    async def initialize_first_metric_credits_entry(self, project: Project, metric: str, session: AsyncSession):
        first_metric_row = await self.fetch_first_metric_data_by_project_name(project, metric, session)
        first_metric_credits_value: MetricCredits = MetricCredits(
            time=first_metric_row.time,
            used_credits=0,
            granted_credits=project.granted_credits,
            metric=metric,
            project_name=project.project_name
        )
        session.add(first_metric_credits_value)
        await session.flush()
        return first_metric_credits_value

    @classmethod
    async def add_metric_credits(
        cls, project: Project, last_metric_credits: MetricCredits,
        credits_value: float, measurement: BaseMeasurement, session: AsyncSession
    ):
        metric_credits: MetricCredits = MetricCredits(
            time=measurement.time,
            used_credits=last_metric_credits.used_credits + credits_value,
            granted_credits=project.granted_credits,
            metric=last_metric_credits.metric,
            project_name=project.project_name
        )
        session.add(metric_credits)
        await session.flush()
        return metric_credits

    # handle own data end ###############################
    #####################################################
    #####################################################

    # general functions end #############################
    #####################################################

    #####################################################
    # compute credits functions #########################

    @classmethod
    async def calculate_credits_with_two_measurements(
        cls, current_measurement: BaseMeasurement, next_measurement: BaseMeasurement, metric: str
    ) -> float:
        if next_measurement.value <= current_measurement.value:
            timescaledb_logger.debug(
                f"Next measurement value {next_measurement} is lower or equal to current "
                f"{current_measurement}, returning 0."
            )
            return 0.0
        value_difference = next_measurement.value - current_measurement.value
        if value_difference > 0:
            return value_difference * config["METRICS_TO_BILL"][metric]
        else:
            return 0.0

    # compute credits functions end #####################
    #####################################################

#     @staticmethod
#     def sanitize_parameter(parameter: str) -> str:
#         """Sanitizes the provided parameter to prevent SQL Injection when querying with
#         user provided content.
#
#         :param parameter: Content to sanitize
#         :return: Sanitized string
#         """
#         # TODO: probably way too restrictive/wrong, but works for now, better fail than
#         # SQL injection
#         critical_chars = {"'", '"', "\\", ";", " ", ","}
#         sanitized_param_chars: List[str] = []
#         for char in parameter:
#             if char in critical_chars:
#                 sanitized_param_chars.append(f"\\{char}")
#             else:
#                 sanitized_param_chars.append(char)
#         sanitized_param = "".join(sanitized_param_chars)
#         if sanitized_param != parameter:
#             influxdb_logger.debug("Sanitized %s to %s", parameter, sanitized_param)
#         return sanitized_param
