"""Contains all leaf subclasses of
:class:`~os_credits.perun.base_attributes.PerunAttribute`.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from .base_attributes import PERUN_DATETIME_FORMAT
from .base_attributes import ContainerPerunAttribute
from .base_attributes import CreditTimestamps
from .base_attributes import ReadOnlyScalarPerunAttribute
from .base_attributes import ScalarPerunAttribute
from .base_attributes import ToEmails
from .exceptions import DenbiCreditsGrantedMissing

PERUN_NAMESPACE_OPT = "urn:perun:group:attribute-def:opt"
PERUN_NAMESPACE_DEF = "urn:perun:group:attribute-def:def"
PERUN_NAMESPACE_GROUP_RESOURCE_OPT = "urn:perun:group_resource:attribute-def:opt"


class DenbiCreditsUsed(
    ScalarPerunAttribute[Optional[Decimal]],
    perun_id=3382,
    perun_friendly_name="denbiCreditsCurrent",
    perun_type="java.lang.String",
    perun_namespace=PERUN_NAMESPACE_OPT,
):
    """Stores the amount of used credits per Project.

    Does currently not use the :class:`~os_credits.credits.base_models.Credits` type,
    which would pin its precision to the value defined in the :ref:`Settings`.
    The amount of credits to bill is rounded to this precision and therefore any change
    in precision of this value must have been caused directly in *Perun* and not by us.

    Stored as string inside *Perun* which is good since the floats **must never be
    stored as such** since it will lead to loss in precision!

    .. todo::

        Fix friendly_name once changed on Perun site, if the change is not performed
        synchronously things will break!
        We used to track spent credits instead of used ones, therefore the current name
        of the attribute inside Perun.
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)

    def perun_deserialize(self, value: Optional[str]) -> Optional[Decimal]:
        return Decimal(value) if value else None

    def perun_serialize(self, value: Optional[Decimal]) -> Optional[str]:
        return None if value is None else str(value)


class DenbiCreditsGranted(
    ReadOnlyScalarPerunAttribute[int],
    perun_id=3383,
    perun_friendly_name="denbiCreditsGranted",
    perun_type="java.lang.String",
    perun_namespace=PERUN_NAMESPACE_OPT,
):
    """Contains the amount of credits granted to the project when its application was
    confirmed.

    Expected to be an integer and implemented read-only since we **MUST** never change
    it since the Cloud portal owns this value and will change it without telling us.
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)

    def perun_deserialize(self, value: Optional[str]) -> int:
        """Stored as str inside perun, unfortunately"""
        if value is None:
            raise DenbiCreditsGrantedMissing()
        return int(float(value))

    def perun_serialize(self, value: int) -> str:
        """Stored as str inside perun, unfortunately"""
        return str(value)


class ToEmail(
    ContainerPerunAttribute[ToEmails],
    perun_id=2020,
    perun_friendly_name="toEmail",
    perun_type="java.util.ArrayList",
    perun_namespace=PERUN_NAMESPACE_DEF,
):
    """Contains the mail addresses of the project administrators.

    Used to send :ref:`Notifications` in case of expired credits or other events.
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)

    def perun_deserialize(self, value: Optional[List[str]]) -> ToEmails:
        return value if value else []


class DenbiCreditTimestamps(
    ContainerPerunAttribute[CreditTimestamps],
    perun_id=3386,
    perun_friendly_name="denbiCreditTimestamps",
    perun_type="java.util.LinkedHashMap",
    perun_namespace=PERUN_NAMESPACE_GROUP_RESOURCE_OPT,
):
    """This attribute is the most important one when it comes to billing projects. It is
    not directly associated to group but to a combination of resource and group where
    the former is associated with the latter.

    The contained dictionary is a mapping between a metric's name and a timestamp of a
    measurement, see :ref:`Metrics and Measurements`. The measurement, whose timestamp
    is stored, is the most recent one used to bill this metric.
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)

    def perun_deserialize(self, value: Optional[Dict[str, str]]) -> CreditTimestamps:
        measurement_timestamps = {}
        if value is not None:
            for measurement_str, timestamp_str in value.items():
                measurement_timestamps.update(
                    {
                        measurement_str: datetime.strptime(
                            timestamp_str, PERUN_DATETIME_FORMAT
                        )
                    }
                )
        return measurement_timestamps

    def perun_serialize(self, value: CreditTimestamps) -> Dict[str, str]:
        return {
            measurement: timestamp.strftime(PERUN_DATETIME_FORMAT)
            for measurement, timestamp in value.items()
        }
