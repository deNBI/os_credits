"""
Exceptions which might be raised during the communication with Perun or processing data
received by it.
"""


class CreditsError(Exception):
    "Base Exception for any exceptions defined by this module"
    pass


class MissingInfluxDatabase(CreditsError):
    "Raised if a required database inside the InfluxDB does not exist"
    pass


class MissingConfigError(CreditsError):
    "Raised if a non-set config value without default value is requested"
    pass


class MeasurementError(CreditsError):
    "Raised if the given measurements cannot be used to calculate credits usage"
    pass


class CalculationResultError(CreditsError):
    "Raised if the result of credits calculation does not meet constraints"
    pass


class MissingTemplateError(CreditsError):
    "Raised if a Notification class does not define a template"
    pass


class BrokenTemplateError(CreditsError):
    "Raised if a template of a Notification class contains errors"
    pass


class MissingToError(CreditsError):
    "Raised if a Notification class does not defined any ``To`` recipients."
