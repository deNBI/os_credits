from enum import Enum

from sqlalchemy import Column, String, Boolean, event, DDL, func, BigInteger, ForeignKey, Float, text
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.ext.declarative import DeferredReflection
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class Credits(Base):
    __tablename__ = "credits"
    time = Column(TIMESTAMP(timezone=True), server_default=text('statement_timestamp()'), primary_key=True)
    used_credits = Column(Float)
    granted_credits = Column(Float)
    project_name = Column(String, ForeignKey("project.project_name"), primary_key=True)
    by_metric = Column(String, primary_key=True)
    metric_time = Column(TIMESTAMP(timezone=True))

    def __repr__(self):
        return f"credits(time={self.time!r}, used_credits={self.used_credits!r}, " \
               f"granted_credits={self.granted_credits!r}, project_name={self.project_name!r}, " \
               f"by_metric={self.by_metric!r})"


event.listen(
    Credits.__table__,
    'after_create',
    DDL(f"SELECT create_hypertable('{Credits.__tablename__}', 'time');")
)


class MetricCredits(Base):
    __tablename__ = "metric_credits"
    time = Column(TIMESTAMP(timezone=True), server_default=text('statement_timestamp()'), primary_key=True)
    used_credits = Column(Float)
    granted_credits = Column(Float)
    project_name = Column(String, ForeignKey("project.project_name"), primary_key=True)
    metric = Column(String, primary_key=True)

    def __repr__(self):
        return f"metric_credits(time={self.time!r}, used_credits={self.used_credits!r}, " \
               f"granted_credits={self.granted_credits!r}, project_name={self.project_name!r}, " \
               f"metric={self.metric!r})"


event.listen(
    MetricCredits.__table__,
    'after_create',
    DDL(f"SELECT create_hypertable('{MetricCredits.__tablename__}', 'time');")
)


class Project(Base):
    __tablename__ = 'project'
    project_name = Column(String, primary_key=True)
    project_name_label_id = Column(BigInteger)
    used_credits = Column(Float, default=0)
    granted_credits = Column(Float, default=0)
    half_limit_reached_send = Column(Boolean, default=False)
    full_limit_reached_send = Column(Boolean, default=False)

    def __repr__(self):
        return f"project(project_name={self.project_name!r}, project_name_label_id={self.project_name_label_id!r}, " \
               f"granted_credits={self.granted_credits!r}, " \
               f"half_limit_reached_send={self.half_limit_reached_send!r}, full_limit_reached_send={self.full_limit_reached_send!r})"

    def __hash__(self):
        return hash(self.project_name)

    def __eq__(self, other):
        return hasattr(other, "project_name") and self.project_name == other.project_name


class PromCatalogReflected(DeferredReflection):
    """
    Baseclass for deferred promscale prom_catalog reflection.
    By calling PromCatalogReflected.prepare(sync_engine instance from SQLAlchemy) all child classes will be reflected.
    """
    __abstract__ = True


class Metric(PromCatalogReflected, Base):
    """
    Reflected promscale prom_catalog.metric table.
    Once reflected this class can be used in SQLAlchemy queries, e.g. select(Metric).where(...).
    Used to get prom_metric.<metric_name> views.
    """
    __tablename__ = "metric"
    __table_args__ = ({"schema": "_prom_catalog"})
    metric_name = Column(String)

    def __repr__(self):
        return f"metric(metric_name={self.metric_name!r})"


class Label(PromCatalogReflected, Base):
    """
    Reflected promscale prom_catalog.label table.
    Once reflected this class can be used in SQLAlchemy queries, e.g. select(Label).where(Label.key == 'project_name').
    """
    __tablename__ = "label"
    __table_args__ = ({"schema": "_prom_catalog"})
    id = Column(BigInteger, primary_key=True)
    key = Column(String)
    value = Column(String)

    def __repr__(self):
        return f"label(id={self.id!r}, key={self.key!r}, value={self.value!r})"


class PromMetricReflected(DeferredReflection):
    """
    Baseclass for deferred promscale prom_metric reflection.
    By calling PromMetricReflected.prepare(sync_engine instance from SQLAlchemy) all child classes will be reflected.
    """
    __abstract__ = True


class BaseMeasurement:
    """
    Baseclass for reflected promscale prom_metric.<metric_name> view.
    """
    time = Column(TIMESTAMP(timezone=True), primary_key=True)
    project_name_id = Column(BigInteger)
    value = Column(Float)

    def __repr__(self):
        return f"measurement(time={self.time!r}, project_name_id={self.project_name_id!r}, value={self.value!r})"


def make_measurement_class(table_name):
    """
    Creates a measurement class.
    Once instantiated and reflected this class be used in SQLAlchemy queries.
    :param table_name: Name of the prom_metric view.
    :return: A Measurement class.
    """
    DynamicBase = declarative_base(class_registry=dict())

    class Measurement(PromMetricReflected, DynamicBase, BaseMeasurement):
        __tablename__ = table_name

        def __repr__(self):
            return f"measurement(time={self.time!r}, project_name_id={self.project_name_id!r}, value={self.value!r})"

        @classmethod
        def _type(cls):
            return BaseMeasurement.__class__.__name__

    return Measurement


class LimitType(str, Enum):
    HALF_LIMIT_REACHED = "half limit reached"
    FULL_LIMIT_REACHED = "full limit reached"
