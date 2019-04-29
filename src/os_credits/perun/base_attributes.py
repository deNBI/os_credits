from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Dict, Generic, List, Type, TypeVar

from . import PERUN_DATETIME_FORMAT

registered_attributes: Dict[str, Type[PerunAttribute[Any]]] = {}


# ValueType
VT = TypeVar("VT")


class PerunAttribute(Generic[VT]):
    displayName: str
    # writable: bool
    _value: VT
    # valueModifiedAt: datetime

    # mapping between the name of a subclass of PerunAttribute and the actual class
    # object, needed to determine the class of a requested attribute of a group, see
    # groupsManager.Group

    # decoder functions for the subattribute of an Attribute
    _subattr_decoder: Dict[str, Callable[[str], Any]] = {
        "valueModifiedAt": lambda value: datetime.strptime(
            value, PERUN_DATETIME_FORMAT
        ),
        "id": int,
    }

    _updated = False

    def __init_subclass__(
        cls,
        perun_id: int,
        perun_friendly_name: str,
        perun_type: str,
        perun_namespace: str
        # perun_namespace: str,
    ) -> None:
        super().__init_subclass__()
        cls.friendlyName = perun_friendly_name
        cls.id = perun_id
        cls.type = perun_type
        cls.namespace = perun_namespace
        if cls.__name__.startswith("_"):
            return
        registered_attributes.update({cls.__name__: cls})

    def __init__(self, value: Any, **kwargs: Any) -> None:
        """
        lala

        """
        self._value = self.perun_decode(value)
        # non-true value means that the attribute does not exist inside perun so there
        # are no further subattributes to decode
        if not self._value:
            return
        for attribute_attr_name in PerunAttribute.__annotations__:
            # ignore any non public attributes here, such as _subattr_decoder
            if attribute_attr_name.startswith("_"):
                continue
            # check whether any parser function is defined and apply it if so
            try:
                if attribute_attr_name in PerunAttribute._subattr_decoder:
                    attribute_attr_value = PerunAttribute._subattr_decoder[
                        attribute_attr_name
                    ](kwargs[attribute_attr_name])
                else:
                    attribute_attr_value = kwargs[attribute_attr_name]
            except KeyError:
                # should only happen in offline mode where e.g. displayMode is not
                # transmitted by Perun
                attribute_attr_value = None
            self.__setattr__(attribute_attr_name, attribute_attr_value)

    def to_perun_dict(self) -> Dict[str, Any]:
        """Serialize the attribute into a dictionary which can passed as JSON content to
        the perun API"""
        return {
            "value": self.perun_encode(self._value),
            "namespace": self.namespace,
            "id": self.id,
            "friendlyName": self.friendlyName,
            "type": self.type,
        }

    def perun_decode(self, value: Any) -> Any:
        return value

    def perun_encode(self, value: Any) -> Any:
        return value

    @classmethod
    def is_resource_bound(cls) -> bool:
        """
        Whether this attribute is not only bound to one specific group but a combination
        of group and resource.
        """
        return "group_resource" in cls.namespace.split(":")

    @property
    def has_changed(self) -> bool:
        """
        Whether the `value` of this attribute has been changed since creation.

        Exposed as function to enable overwriting in subclasses.
        """
        return self._updated

    @has_changed.setter
    def has_changed(self, value: bool) -> None:
        if not isinstance(value, bool):
            raise TypeError("`has_changed` must be of type bool.")
        self._updated = value

    def __str__(self) -> str:
        return str(self._value)

    def __repr__(self) -> str:
        # This assumes that either the container value evaluates to false or in the
        # scalar case that the attribute is None
        if not self._value:
            return f"{type(self).__name__}(value=None)"
        param_repr: List[str] = [f"value={self._value}"]
        for attribute in filter(
            lambda attribute: not attribute.startswith("_"), self.__annotations__.keys()
        ):
            # None of the values are set in offline mode
            if attribute in dir(self):
                param_repr.append(f"{attribute}={repr(getattr(self, attribute))}")

        return f"{type(self).__name__}({','.join(param_repr)})"

    def __bool__(self) -> bool:
        return bool(self._value)


class _ScalarPerunAttribute(
    PerunAttribute[VT],
    # class definition must contain the following attributes to allow 'passthrough' from
    # child classes
    perun_id=None,
    perun_friendly_name=None,
    perun_type=None,
    perun_namespace=None,
):
    """
    Base class for scalar attributes, where `value` only contains a scalar value, i.e.
    an `float` or `str`, in contrast to container attributes
    """

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)

    @property
    def value(self) -> VT:
        return self._value

    @value.setter
    def value(self, value: Any) -> None:
        # only check for type correctness if we already have a value
        if self._value and not isinstance(value, type(self._value)):
            raise TypeError(
                f"Value must be of the same type as current one ({type(self.value)})"
            )
        if self.value != value:
            self._updated = True
            self._value = value


ToEmails = List[str]
CreditTimestamps = Dict[str, datetime]
# ContainerValueType, used to ensure type checker, that the classes define a `.copy`
# method
CVT = TypeVar("CVT", ToEmails, CreditTimestamps)


class _ContainerPerunAttribute(
    PerunAttribute[CVT],
    # class definition must contain the following attributes to allow 'passthrough' from
    # child classes
    perun_id=None,
    perun_friendly_name=None,
    perun_type=None,
    perun_namespace=None,
):
    """
    Base class for container attributes, i.e. ToEmails where the `value` is a list of
    the actual mail addresses.

    The `has_changed` logic of PerunAttribute has to be overwritten for this classes
    since any changes during runtime are not reflected by updating `value` as an
    attribute but by updating its contents. Therefore `value` does not have a setter.
    """

    _value: CVT
    _value_copy: CVT

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        # the super class will set self._value and we will save a copy here
        # also ensures that `value` of a container attribute will never be None
        self._value_copy = self._value.copy()

    @property
    def has_changed(self) -> bool:
        """
        Since the value of this attribute is a dictionary the setter approach of the
        superclass to detect changes does not work. Instead we compare the current
        values with the initial ones.
        """
        return self._value_copy != self._value

    @has_changed.setter
    def has_changed(self, value: bool) -> None:
        if not value:
            # reset changed indicator
            self._value_copy = self._value.copy()
            return
        raise ValueError("Manually setting to true not supported")

    @property
    def value(self) -> CVT:
        return self._value