from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from decimal import Decimal
from typing import Any
from typing import ClassVar
from typing import Dict
from typing import NewType
from typing import Type
from typing import TypeVar

from os_credits.exceptions import MeasurementError
from os_credits.influx.helper import InfluxSerializer
from os_credits.influx.model import InfluxDBPoint
from os_credits.log import internal_logger
from os_credits.settings import config

REGISTERED_MEASUREMENTS = {}

Credits = NewType("Credits", Decimal)
"""Distinct Credits type to prevent mixing of regular Decimals and Credits, since we
MUST apply quantize to every instance of it."""


class CreditsSerializer(InfluxSerializer, types=["Credits"]):
    """Implementation of the :class:`~os_credits.influx.helper.InfluxSerializer`
    interface to be able to store our new :class:`Credits` inside *InfluxDB*.
    """

    @staticmethod
    def serialize(value: Any) -> float:
        return float(value)

    @staticmethod
    def deserialize(value: Any) -> Credits:
        return Credits(Decimal(value).quantize(config["OS_CREDITS_PRECISION"]))


@dataclass(init=False, frozen=True)
class UsageMeasurement(InfluxDBPoint):
    """Base data class of all usage measurements. Cannot be used directly since no
    metric is attached to it which is why ``init=False``.

    Pure dataclasses which should not be extended with any kind of logic other than
    holding information.
    """

    location_id: int = field(metadata={"tag": True})
    """The ID of the ``Resource`` in *Perun* representing the location from which this
    measurement has been received.

    Must be present to bill a group based on this measurement.
    """

    project_name: str = field(metadata={"tag": True})
    """The name of the project to which this measurement belongs.
    """

    value: float
    """Value of the measurement, stored by Prometheus inside this field when using
    InfluxDB as remote storage.
    """

    metric: Type[Metric] = field(repr=False, init=False, compare=False)
    """Every Measurement class must be connected with a metric, since the latter
    contains the logic to bill a project based on ``value``.

    :attr:`Metric.name` is also used to determine which measurement should be created
    based on the content of an incoming usage measurement in Influx Line protocol.
    """

    def __init_subclass__(cls: Type[UsageMeasurement]) -> None:
        REGISTERED_MEASUREMENTS[cls.metric.name] = cls


# MeasurementType
MT = TypeVar("MT", bound=UsageMeasurement)


class Metric:
    """Metrics hold the information and logic how to bill measurements.

    The essential functions of every Metric are :func:`calculate_credits` and
    :func:`costs_per_hour`.
    """

    _metrics_by_name: Dict[str, Type[Metric]] = {}
    metrics_by_friendly_name: Dict[str, Type[Metric]] = {}

    name: ClassVar[str]
    """Corresponds to the name of a measurement stored inside *InfluxDB*.

    This name is also used by :func:`~os_credits.credits.models.measurement_by_name` to
    determine whether a submitted measurement is billable or not.
    """
    friendly_name: ClassVar[str]
    """Human readable name of the metric.
    """
    description: ClassVar[str] = ""
    """Provides further information about the metric, i.e. that the
    :class:`~os_credits.credits.models.RAMMetric` contains the amount of used Memory in
    MiB.
    """

    def __init_subclass__(cls, name: str, friendly_name: str) -> None:
        if None in (name, friendly_name):
            internal_logger.debug(
                "Not registering subclass %s of `Metric` since one or both of its "
                "names are None."
            )
            return
        if name in Metric._metrics_by_name:
            raise ValueError(
                f"Metric with name {name} is already registered: "
                f"{Metric._metrics_by_name[name]}"
            )
        if friendly_name in Metric.metrics_by_friendly_name:
            raise ValueError(
                f"Metric with friendly_name {friendly_name} is already registered: "
                f"{Metric.metrics_by_friendly_name[friendly_name]}"
            )
        Metric._metrics_by_name.update({name: cls})
        Metric.metrics_by_friendly_name.update({friendly_name: cls})
        cls.name = name
        cls.friendly_name = friendly_name
        internal_logger.debug("Registered subclass of `Metric`: %s", cls)

    @classmethod
    def calculate_credits(
        cls, *, current_measurement: MT, older_measurement: MT
    ) -> Credits:
        """Given two measurements determine how many credits should be billed. This
        function should not be called directly but rather through the high level
        function :func:`~os_credits.credits.billing.calculate_credits`.

        To prevent mistakes the arguments are keyword only. Defining their type as
        ``MT`` (:data:`MT`) shows a type checker that both arguments should be the same
        (sub)class of :class:`UsageMeasurement`.

        :param current_measurement: The measurement submitted by *InfluxDB* which is
            processed by the current task. Represents the most recent measurement of
            this metric.
        :param older_measurement: Measurement of the same type as
            :attr:`current_measurement` which is the most recent one on whose basis
            credits have been billed.
        """
        raise NotImplementedError("Must be implemented by subclass.")

    @classmethod
    def api_information(cls) -> Dict[str, Any]:
        """
        Returns a dictionary containing the description and type information of this
        metric.

        :return: Dictionary holding information about this metric, see
            :func:`costs_per_hour` to understand the relevance of ``type``.
        """
        return {
            "type": "int",
            "description": cls.description,
            "name": cls.name,
            "friendly_name": cls.friendly_name,
        }

    @classmethod
    def costs_per_hour(cls, spec: Any) -> Credits:
        """Used by the :func:`~os_credits.views.costs_per_hour` endpoint to calculate
        the projected costs per hour of a virtual machine.

        :param spec: Of the same type as ``type`` of :func:`api_information`, indicates
            the amount of resources used by the machine to be billed, e.g. the amount of
            vCPU or MiB of RAM.
        """
        raise NotImplementedError("Must be implemented by subclass.")


class TotalUsageMetric(
    Metric,
    # class definition must contain the following attributes to allow 'passthrough' from
    # child classes
    name=None,
    friendly_name=None,
):
    """Base class of every metric which is only billed by its usage values, i.e. the
    timestamps of any measurements are not considered at all.

    One example is the :class:`~os_credits.credits.models.VCPUMetric` which bills the
    amount of used virtual CPUs, the value of its corresponding measurement represents
    this time in hours.
    """

    CREDITS_PER_VIRTUAL_HOUR: ClassVar[Decimal]
    """Amount of credits which have to be paid for every virtual hour of usage of this
    resource. Explicitly not defined as :class:`Credits` since the price of one virtual
    hour may be lower than the precision given by ``OS_CREDITS_PRECISION``,
    :ref:`Settings`, which means that no :func:`~decimal.Decimal.quantize` has been
    applied to it, therefore it is no ``Credits`` type."""

    def __init_subclass__(cls, **kwargs: Any) -> None:
        if cls.CREDITS_PER_VIRTUAL_HOUR <= 0:
            raise ValueError(
                f"Metric type {cls.__name__} has non-positive "
                f"CREDITS_PER_VIRTUAL_HOUR({cls.CREDITS_PER_VIRTUAL_HOUR})."
            )
        super().__init_subclass__(**kwargs)

    @classmethod
    def costs_per_hour(cls, spec: Decimal) -> Credits:
        return Credits(
            (spec * cls.CREDITS_PER_VIRTUAL_HOUR).quantize(
                config["OS_CREDITS_PRECISION"]
            )
        )

    @classmethod
    def calculate_credits(
        cls, *, current_measurement: MT, older_measurement: MT
    ) -> Credits:
        """
        Base implementation how to bill two measurements of the same type. Expected to
        be overwritten by subclasses whose billing logic goes beyond subtracting usage
        values, e.g. if your current_measurement values are not continuously increasing
        but fluctuating, i.e. being a delta instead of a total sum.
        """
        if current_measurement.metric is not older_measurement.metric:
            raise TypeError("Measurements must be of same type")
        if current_measurement.timestamp < older_measurement.timestamp:
            raise MeasurementError(
                "Passed current_measurement must be older. Use the top-level "
                "`calculate_credits` function to prevent this error."
            )
        return Credits(
            (
                (Decimal(current_measurement.value) - Decimal(older_measurement.value))
                * cls.CREDITS_PER_VIRTUAL_HOUR
            ).quantize(config["OS_CREDITS_PRECISION"])
        )
