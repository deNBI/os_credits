from os_credits.exceptions import CreditsError


class PerunBaseException(CreditsError):
    "Base exception class for all errors occurring during communication with Perun"
    pass


class BadCredentialsException(PerunBaseException):
    "Raised if Perun returns an 'Unauthorized' status code"
    pass


class GroupNotExistsError(PerunBaseException):
    "Python mapping of Perun's GroupNotExistsException"
    pass


class InternalError(PerunBaseException):
    "Python mapping of Perun's InternalErrorException"
    pass


class ConsistencyError(PerunBaseException):
    "Python mapping of Perun's ConsistencyErrorException"
    pass


class RequestError(PerunBaseException):
    "Generic Exception in case no specific exception has been thrown"
    pass


class GroupResourceNotAssociatedError(PerunBaseException):
    "Raised if a group and a resource are not associated but should be"
    pass


class GroupAttributeError(CreditsError):
    "Base Exception if any Group-Attribute has an invalid value"
    pass


class DenbiCreditsUsedMissing(GroupAttributeError):
    """Raised if a group does not have any value for
    :class:`~os_credits.perun.attributes.DenbiCreditsUsed` and has been billed before"""

    pass


class DenbiCreditsGrantedMissing(GroupAttributeError):
    """Raised if a group does not have any credits granted in which case we cannot
    operate on it."""

    pass
