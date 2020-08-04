from __future__ import annotations

from functools import lru_cache
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Type
from typing import TypeVar

from os_credits.log import internal_logger

from .attributes import DenbiCreditsGranted
from .attributes import DenbiCreditsUsed
from .attributes import DenbiCreditTimestamps
from .attributes import ToEmail
from .attributesManager import get_attributes
from .attributesManager import get_resource_bound_attributes
from .attributesManager import set_attributes
from .attributesManager import set_resource_bound_attributes
from .base_attributes import PerunAttribute
from .exceptions import GroupResourceNotAssociatedError
from .groupsManager import get_group_by_name
from .resourcesManager import get_assigned_resources

GTV = TypeVar("GTV", bound="Group")


class Group:
    """
    Represents a Group object inside Perun.

    Every attribute requested via class variable annotations, where the annotation is a
    subclass of :class:`~os_credits.perun.base_attributes.PerunAttribute`, will be set
    on the class with the chosen name. This happens during `connect()` which tries to
    fill the Attributes with information from Perun.
    """

    name: str
    """Name of this group. Always set on instantiated objects since it is required by
    the constructor.
    """

    resource_id: int
    """ID of the resource to use when retrieving resource bound attributes. Also
    required by constructor.
    """

    assigned_resource: Optional[bool] = None
    """Indicator whether this group is actually assigned to resource with
    ``resource_id``. Initially None, set to bool when :func:`connect` is called, but
    only if any of the annotated
    :class:`~os_credits.perun.base_attributes.PerunAttribute` subclasses are *resource
    bound* attributes.
    """

    id: int
    """ID of this group inside Perun. Initially unset, also populated by
    :func:`connect`.
    """

    email: ToEmail
    """Initially unset, populated on :func:`connect`. See
    :class:`~os_credits.perun.attributes.ToEmail`.
    """
    credits_granted: DenbiCreditsGranted
    """Initially unset, populated on :func:`connect`. See
    :class:`~os_credits.perun.attributes.DenbiCreditsGranted`.
    """
    credits_used: DenbiCreditsUsed
    """Initially unset, populated on :func:`connect`. See
    :class:`~os_credits.perun.attributes.DenbiCreditsUsed`.
    """
    credits_timestamps: DenbiCreditTimestamps
    """Initially unset, populated on :func:`connect`. See
    :class:`~os_credits.perun.attributes.DenbiCreditTimestamps`.
    """

    def __init__(self, name: str, resource_id: int = 0) -> None:
        """
        Calling this method only creates the object, but does not query perun yet. Call
        :func:`connect` to populate all Perun attributes with content.

        :param name: Name of group inside Perun. Unique inside the de.NBI VO
        :param resource_id: ID of the resource to use when retrieving resource bound
            attributes.
        """
        self.name = name
        self.resource_id = resource_id

    async def connect(self: GTV) -> GTV:
        """Retrieve all required values from *Perun* and populate the rest of the
        variables of this instance.

        #. The ID of this group is determined by calling
           :func:`~os_credits.perun.groupsManager.get_group_by_name` since it is needed
           by the next methods.
        #. :func:`get_perun_attributes` is used to determine
           which attributes of the class must be retrieved from *Perun*.

            #. If any of these attributes are *resource bound* we check whether the
               resource whose ID is stored in :attr:`resource_id` is actually associated
               with this group. This check is necessary since *Perun* is happy to return
               and store attributes of *invalid* combinations of groups and resources.
               The result is stored in :attr:`assigned_resource` and performed by
               :func:`is_assigned_resource`.

        #. All attributes of the group are retrieved by calling
           :func:`~os_credits.perun.attributesManager.get_attributes` and
           :func:`~os_credits.perun.attributesManager.get_resource_bound_attributes` if
           necessary and stored inside this group.

        :return: Self, to allow chaining such as ``g=await Group([...]).connect()``
        :raises GroupResourceNotAssociatedError: In case group :attr:`name` is not
            assigned to resource with :attr:`resource_id` inside *Perun*.
        :raises ~os_credits.perun.exceptions.PerunBaseException: Or any other subclass
            of exception indicating errors during communication with perun.
        """
        group_response = await get_group_by_name(self.name)
        self.id = int(group_response["id"])

        # Mappings between the names of perun attributes needed for this instance and
        # the friendlyName of the actual attributes
        # friendlyName -> name_used_in_instance
        friendly_name_to_group_attr_name: Dict[str, str] = {}

        requested_attributes: List[str] = []
        requested_resource_bound_attributes: List[str] = []

        for (attr_name, attr_class) in type(self).get_perun_attributes().items():
            friendly_name_to_group_attr_name[attr_class.friendlyName] = attr_name
            if attr_class.is_resource_bound():
                requested_resource_bound_attributes.append(attr_class.get_full_name())
            else:
                requested_attributes.append(attr_class.get_full_name())
        # will hold the contents of all retrieved attributes
        attributes: Dict[str, Dict[str, Any]] = {}
        if requested_attributes:
            for attr in await get_attributes(
                self.id, attribute_full_names=requested_attributes
            ):
                attributes[attr["friendlyName"]] = attr

        if requested_resource_bound_attributes:
            #self.assigned_resource = await self.is_assigned_resource()
            #if not self.assigned_resource:
                # raise GroupResourceNotAssociatedError(
                #    f"Group `{self.name}` is not associated with resource with id "
                #    f"`{self.resource_id}` but resource bound attributes have been "
                #    "requested "
                #)
            for attr in await get_resource_bound_attributes(
                self.id,
                self.resource_id,
                attribute_full_names=requested_resource_bound_attributes,
            ):
                attributes[attr["friendlyName"]] = attr
        internal_logger.debug(
            "Retrieved attributes Group %s: %s",
            self,
            {attr_name: attr["value"] for attr_name, attr in attributes.items()},
        )
        for friendly_name, group_attr_name in friendly_name_to_group_attr_name.items():
            attr_class = type(self).get_perun_attributes()[group_attr_name]

            try:
                setattr(self, group_attr_name, attr_class(**attributes[friendly_name]))
            except KeyError:
                # in case we got no content for this attribute by perun
                setattr(self, group_attr_name, attr_class(value=None))

        return self

    async def is_assigned_resource(self) -> bool:
        """Explicit check whether the resource :attr:`resource_id` is associated with
        group :attr:`name`.

        Does so by testing whether :attr:`resource_id` is part of the response of
        :func:`~os_credits.perun.resourcesManager.get_assigned_resources`
        """
        # using a generator expression saves time
        return self.resource_id in (
            resource["id"] for resource in await get_assigned_resources(self.id)
        )

    async def save(self, _save_all: bool = False) -> None:
        """Collects all annotated
        :class:`~os_credits.perun.base_attributes.PerunAttribute` of this group and
        sends/saves them to *Perun* in case their value has changed since retrieval.

        Uses the :attr:`~os_credits.perun.base_attributes.PerunAttribute.has_changed`
        attribute.

        :param _save_all: Save all attributes regardless whether their value was
            actually changed since retrieval. Also used for testing.
        """
        internal_logger.debug("Save of Group %s called", self)
        changed_attrs: List[PerunAttribute[Any]] = []
        changed_resource_bound_attrs: List[PerunAttribute[Any]] = []
        # collect all attributes whose value has changed since retrieval
        for attribute_name in type(self).get_perun_attributes():
            attr = getattr(self, attribute_name)
            # save all attributes in offline/dummy since we will not get non-stored back
            # from Perun
            if attr.has_changed or _save_all:
                if not attr.is_resource_bound():
                    changed_attrs.append(attr)
                else:
                    changed_resource_bound_attrs.append(attr)
        if changed_attrs:
            internal_logger.debug(
                "Sending modified regular attributes to perun %s", changed_attrs
            )
            await set_attributes(self.id, changed_attrs)
        if changed_resource_bound_attrs:
            #tlin: changed this bc it always did not work
            if True: #getattr(self, "assigned_resource", False):
                internal_logger.debug(
                    "Sending modified resource bound attributes to perun %s",
                    changed_resource_bound_attrs,
                )
                await set_resource_bound_attributes(
                    self.id, self.resource_id, changed_resource_bound_attrs
                )
            else:
                internal_logger.warning(
                    "Not sending modified attribute to perun, since Group %s is not "
                    "associated with resource with id %s. How did we even retrieve any "
                    "such attributes?",
                    self.name,
                    self.resource_id,
                )

    def __repr__(self) -> str:
        # in case Group has not been connected yet
        if getattr(self, "id", None) is None:
            return f"Group({self.name},{self.resource_id})"
        param_repr: List[str] = []
        for attribute in type(self).get_perun_attributes():
            param_repr.append(f"{attribute}={repr(self.__getattribute__(attribute))}")

        # using square instead of regular brackets to indicate that you cannot copy
        # paste this output to construct a group
        return f"Group[{','.join(param_repr)}]"

    def __str__(self) -> str:
        return f"{self.name}@{self.resource_id}"

    def __setattr__(self, name: str, value: Any) -> None:
        if name in type(self).get_perun_attributes() and not isinstance(
            value, PerunAttribute
        ):
            raise AttributeError(
                "PerunAttributes must not be replaced by non-PerunAttributes. Update "
                "the attribute's `value` attribute instead."
            )
        object.__setattr__(self, name, value)

    @classmethod
    @lru_cache()
    def get_perun_attributes(cls) -> Dict[str, Type[PerunAttribute[Any]]]:
        """
        Return all class attributes which are annotated with subclasses of
        :class:`~os_credits.perun.base_attributes.PerunAttribute`.

        Since the content of the response cannot change at runtime a
        :func:`~functools.lru_cache` is used.

        :return: Dictionary of the attribute names of this class and the corresponding
            :class:`~os_credits.perun.base_attributes.PerunAttribute` subclass.
        """
        attributes = {}
        for attr_name, attr_class_name in cls.__annotations__.items():
            try:
                attributes[attr_name] = PerunAttribute.registered_attributes[
                    attr_class_name
                ]
                internal_logger.debug(
                    "Connected group attribute `%s` with PerunAttribute `%s`",
                    attr_name,
                    attr_class_name,
                )
            except KeyError:
                # this will fail for any non-Perun attribute, such as name or id
                pass
        return attributes
