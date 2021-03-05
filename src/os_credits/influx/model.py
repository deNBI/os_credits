from __future__ import annotations

from dataclasses import MISSING
from dataclasses import dataclass
from dataclasses import fields
from datetime import datetime
from typing import Any
from typing import AnyStr
from typing import Dict
from typing import List
from typing import Type
from typing import TypeVar

from os_credits.log import internal_logger

from .helper import deserialize
from .helper import serialize

INFLUX_QUERY_DATE_FORMAT = "%Y-%m-%d %H:%M:%S.%f"

_DEFINITELY_PAST = datetime.fromtimestamp(0)

# PointType
PT = TypeVar("PT", bound="InfluxDBPoint")


@dataclass(frozen=True)
class InfluxDBPoint:
    """Base class of all data models whose content is written or read from the InfluxDB.

    To define a data model as shown in the official InfluxDB Line Tutorial extend in the
    following way

    >>> from dataclasses import dataclass, field
    >>> from os_credits.influx.model import InfluxDBPoint
    >>> @dataclass(frozen=True)
    ... class Weather(InfluxDBPoint):
    ...     temperature: int
    ...     location: str = field(metadata={'tag': True})
    ...     static_id: int = 5
    >>> from datetime import datetime
    >>> timestamp = datetime(2016, 6, 13, 19, 43, 50, 100400)
    >>> # the first two parameters are defined inside ``InfluxDBPoint``
    >>> weather = Weather('weather', timestamp, 82, 'us-midwest')
    >>> print(weather.to_lineprotocol())
    b'weather,location=us-midwest temperature=82 1465839830100399872'
    >>> Weather.from_lineprotocol(weather.to_lineprotocol()) == weather
    True

    We are using the ``metadata`` field of :class:`~dataclasses.dataclass` to indicate
    whether to store a date as field or as tag. The difference between them is that tags
    are indexed by InfluxDB. Attributes with a default value are currently ignored, if a
    change to this should be necessary, whether to skip an attribute or not should be
    indicated via the ``metadata``.

    All subclasses must also be frozen, since this base class is. Use the
    :func:`dataclasses.replace` method instead. Allows us to use the instances as
    dictionary keys.

    Unfortunately *InfluxDB* does store all timestamps as nanoseconds which are not
    natively supported by python. We are therefore losing some precision but this is
    negligible since the timestamps of *Prometheus* are only milliseconds.
    """

    measurement: str
    """The name of this measurement"""
    timestamp: datetime

    @classmethod
    def from_iterpoint(cls: Type[PT], *values: List[Any], meta: Dict[str, str]) -> PT:
        """Only intended to be passed to the ``iterpoints`` method of ``aioinflux`` to
        parse the points and construct valid InfluxDBPoint instances. See its
        documentation for a description of the contents of ``values`` and ``meta``.

        The metadata of dataclass attributes are used to parse and convert the necessary
        information, unknown values and tags are dropped.

        :param cls: Subclass of :class:`InfluxDBPoint` on which this method is called.
            Instances of this class will be tried to be constructed given the returned
            data from the InfluxDB and returned.
        """
        measurement_name = meta["name"]
        combined_dict = dict(zip(meta["columns"], values))
        args: Dict[str, Any] = {
            "measurement": measurement_name,
            "timestamp": deserialize(combined_dict["time"], datetime),
        }
        for f in fields(cls):
            if f.default is not MISSING:
                continue
            # values of this fields are already known
            if f.name in args:
                continue
            try:
                args[f.name] = deserialize(combined_dict[f.name], f)
            except Exception as e:
                internal_logger.info(str(e) + "values were: %s, %s", combined_dict[f.name], f)
        new_point = cls(**args)
        internal_logger.debug("Constructed %s", new_point)
        return new_point

    @classmethod
    def from_lineprotocol(cls: Type[PT], influx_line_: AnyStr) -> PT:
        """
        Creates a point from an InfluxDB Line, see
        https://docs.influxdata.com/influxdb/v1.7/write_protocols/line_protocol_tutorial/

        Deliberate usage of ``cls`` to allow and support potential subclassing. If the
        line contains more information than defined by ``cls`` the rest is simply
        ignored.

        >>> from os_credits.influx.model import InfluxDBPoint
        >>> line = b'weather,location=us-midwest temperature=82 1465839830100399872'
        >>> InfluxDBPoint.from_lineprotocol(line)
        InfluxDBPoint(measurement='weather', timestamp=datetime.datetime(2016, 6, 13, 19, 43, 50, 100400))

        :param cls: Subclass on which this method is called. Instances of this class
            will be the return type.
        :param influx_line_: Influx Line to parse, either ``string`` or ``bytes``.
        :return: Instances of `cls`.
        :raises KeyError: Attribute of ``cls`` without default value not present in line
        """
        if isinstance(influx_line_, bytes):
            influx_line = influx_line_.decode()
        else:
            influx_line = influx_line_
        internal_logger.debug("Converting InfluxDB Line `%s`", influx_line)
        measurement_and_tag, field_set, timestamp_str = influx_line.strip().split()
        measurement_name, tag_set = measurement_and_tag.split(",", 1)
        tag_field_dict: Dict[str, str] = {}
        for tag_pair in tag_set.split(","):
            tag_name, tag_value = tag_pair.split("=", 1)
            tag_field_dict.update({tag_name: tag_value})
        for field_pair in field_set.split(","):
            field_name, field_value = field_pair.split("=", 1)
            tag_field_dict.update({field_name: field_value})
        # we know how to deserialize those
        args: Dict[str, Any] = {
            "measurement": measurement_name,
            "timestamp": deserialize(timestamp_str, datetime),
        }
        for f in fields(cls):
            # currently not serialized, see class documentation
            if f.default is not MISSING:
                continue
            # values of this fields are already known
            if f.name in args:
                continue
            is_tag = False
            if f.metadata and f.metadata.get("tag", False):
                is_tag = True
            if f.name not in tag_field_dict:
                raise KeyError(
                    f"InfluxDB Line does not contain {'tag' if is_tag else 'field'} "
                    "`{f.name}`"
                )
            value = tag_field_dict[f.name]
            # string field values are quoted, strip them
            if not is_tag and isinstance(value, str):
                value = value.strip('"')
            args[f.name] = deserialize(value, f)
        new_point = cls(**args)
        internal_logger.debug("Constructed %s", new_point)
        return new_point

    def to_lineprotocol(self) -> bytes:
        """Serializes this (subclass of) :class:`InfluxDBPoint` to its representation in
        Influx Line Protocol.

        Not called directly by our code but by ``aioinflux``. Whenever an object should
        be stored inside an InfluxDB and this object defines a ``to_lineprotocol``
        method it is used for serialization. Duck-typing for the win!

        :return: Serialization in Influx Line Protocol.
        """
        tag_dict: Dict[str, str] = {}
        field_dict: Dict[str, str] = {}
        measurement = self.measurement
        timestamp = format(serialize(self.timestamp), ".0f")
        for f in fields(self):
            # we know how to serialize those
            if f.name in {"measurement", "timestamp"}:
                continue
            # currently not serialized, see class documentation
            if f.default is not MISSING:
                continue
            value = getattr(self, f.name)
            if f.metadata and f.metadata.get("tag", False):
                tag_dict[f.name] = str(serialize(value, f))
            else:
                component_value = serialize(value, f)
                # string field values must be quoted
                if isinstance(component_value, str):
                    field_dict[f.name] = str(serialize(f'"{component_value}"', f))
                else:
                    field_dict[f.name] = str(serialize(component_value, f))
        tag_str = ",".join(f"{key}={value}" for key, value in tag_dict.items())
        field_str = ",".join(f"{key}={value}" for key, value in field_dict.items())
        influx_line = " ".join(
            [",".join([measurement, tag_str]), field_str, str(timestamp)]
        )
        return influx_line.encode()
