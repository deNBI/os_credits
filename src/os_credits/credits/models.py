from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from decimal import Decimal
from typing import AnyStr
from typing import Type

from os_credits.influx.model import InfluxDBPoint

from .base_models import REGISTERED_MEASUREMENTS
from .base_models import Credits
from .base_models import Metric
from .base_models import TotalUsageMetric
from .base_models import UsageMeasurement
from os_credits.settings import config


class VCPUMetric(TotalUsageMetric, name="project_vcpu_usage", friendly_name="cpu"):

    CREDITS_PER_VIRTUAL_HOUR = config["VCPU_CREDIT_PER_HOUR"]
    description = "Amount of vCPUs."


class RAMMetric(TotalUsageMetric, name="project_mb_usage", friendly_name="ram"):

    # always specify the amount as string to prevent inaccuracies of builtin float
    CREDITS_PER_VIRTUAL_HOUR = config["RAM_CREDIT_PER_HOUR"]
    description = (
        "Amount of RAM in GiB."
    )


# located next to the metrics to ensure that their classes are initialized and therefore
# registered in REGISTERED_MEASUREMENTS
def measurement_by_name(name: AnyStr) -> Type[UsageMeasurement]:
    """Returns the correct :class:`UsageMeasurement` subclass corresponding to the given
    Influx Line.

    The measurement itself does not know its name, but its connected metric does.

    :param name: The name of the measurement or a text in InfluxDB Line Protocol from
        which the name is extracted.
    :return: Subclass of :class:`~os_credits.credits.base_models.UsageMeasurement`
        responsible for this measurement.
    :raises ValueError: No
        :class:`~os_credits.credits.base_models.UsageMeasurement`
        responsible/available, i.e. the passed measurement is not needed/supported.
    """
    if isinstance(name, bytes):
        influx_line = name.decode()
    else:
        influx_line = name

    measurement_name = influx_line.split(",", 1)[0]
    try:
        return REGISTERED_MEASUREMENTS[measurement_name]
    except KeyError:
        raise ValueError(f"Measurement `{name}` it not supported/needed.")


@dataclass(frozen=True)
class VCPUMeasurement(UsageMeasurement):
    metric: Type[Metric] = VCPUMetric


@dataclass(frozen=True)
class RAMMeasurement(UsageMeasurement):
    metric: Type[Metric] = RAMMetric


@dataclass(frozen=True)
class BillingHistory(InfluxDBPoint):
    """Whenever a project/group is successfully billed, meaning the amount used credits
    has changed, we store the relevant data of the transaction inside the *InfluxDB*.

    The name of the group/project is used as ``measurement`` and ``timestamp`` is the
    timestamp of the measurement which caused the billing.

    See :ref:`Credits History`.
    """

    credits_used: Credits
    """Amount of credits used for the project **after** the billing.
    """
    metric_name: str = field(metadata={"tag": True})
    """Name of the metric of the measurement which caused the billing.
    """
    metric_friendly_name: str = field(metadata={"tag": True})
    """Human readable name of the metric of the measurement which caused the billing.
    """
