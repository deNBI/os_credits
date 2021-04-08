"""All settings can be set/overwritten by environment variables of the same name. They
are set when the :mod:`os_credits.settings` module is loaded.

The settings can be accessed via the :attr:`config` dictionary which is a
:class:`collections.ChainMap` containing the parsed and processed environment variables,
the default config values and a special dictionary :class:`_EmptyConfig` whose only
purpose is to log any access to non existing settings and raise a
:exc:`~os_credits.exceptions.MissingConfigError`.
"""

from __future__ import annotations

from collections import ChainMap
from collections import UserDict
from decimal import Decimal
from os import environ
from typing import Any
from typing import Dict
from typing import Optional
from typing import Set
from typing import cast

from mypy_extensions import TypedDict

from os_credits.exceptions import MissingConfigError
from os_credits.log import internal_logger


class Config(TypedDict):
    """Used to keep track of all available settings and allows the type checker to infer
    the types of individual keys/settings.

    .. envvar:: CLOUD_GOVERNANCE_MAIL

        Mail address of the *de.NBI Cloud Governance*. Entered as ``Cc`` when sending
        certain :ref:`Notifications` such as warnings about credit usage.

    .. envvar:: CREDITS_HISTORY_DB

        Name of the database inside the InfluxDB in which to store the
        :class:`~os_credits.credits.models.BillingHistory` objects. The database must
        already exist when the application is launched and correct permissions have to
        be set.

        Default: ``credits_history``

    .. envvar:: INFLUXDB_DB

        Name of the database inside InfluxDB used as storage backend by *Prometheus*.

    .. envvar:: INFLUXDB_HOST

        Hostname of the InfluxDB.

        Default: ``localhost``

    .. envvar:: INFLUXDB_PORT

        Port to use to connect to the InfluxDB.

        Default: ``8086``

    .. envvar:: INFLUXDB_USER

        Username to send during authentication.

    .. envvar:: INFLUXDB_USER_PASSWORD

        Password to send during authentication.

    .. envvar:: MAIL_FROM

        Value of the ``From`` header when sending :ref:`Notifications`.

        Default: ``CreditsService@denbi.de``
    .. envvar:: MAIL_NOT_STARTTLS

        Whether to skip the attempt to use a **STARTTLS** secured connection with the
        SMTP server. Set to true if the variable is present in the environment. Should
        only be used for tests and not in production!

        Default: ``False``

    .. envvar:: MAIL_SMTP_PASSWORD

        Password to send during authentication.

    .. envvar:: MAIL_SMTP_PORT

        Port to use to contact the SMTP server.

        Default: ``25``

    .. envvar:: MAIL_SMTP_USER

        Username to send during authentication.

    .. envvar:: MAIL_SMTP_SERVER

        Hostname or address of the SMTP server.

        Default: ``localhost``

    .. envvar:: NOTIFICATION_TO_OVERWRITE

        If this setting contains a non-empty value, which should be valid email address,
        all notifications (see :ref:`Notifications`) are exclusively sent to it. All
        other receivers (``To``, ``Cc`` and ``Bcc``) are omitted.

    .. envvar:: OS_CREDITS_PERUN_LOGIN

        Login to use when authenticating against Perun.

    .. envvar:: OS_CREDITS_PERUN_PASSWORD

        Password to use when authenticating against Perun.

    .. envvar:: OS_CREDITS_PERUN_VO_ID

        ID of our Virtual Organisation, needed to retrieve attributes from Perun.

    .. envvar:: OS_CREDITS_PRECISION

        Specifies to how many decimal places credits should be rounded during a billing.
        Internally credits are stored as :class:`decimal.Decimal` objects and rounding
        is done via :func:`decimal.Decimal.quantize`, see the `Decimal FAQ` in the
        Python docs.

        Default: ``2``

    .. envvar:: OS_CREDITS_PROJECT_WHITELIST

        If set in the environment its content must be a semicolon separated list of
        project names which should be billed exclusively. Measurements of every other
        project are ignored.

    .. envvar:: OS_CREDITS_WORKERS

        Number of task workers spawned at start of the application which will process
        new InfluxDB lines put into queue by the endpoint handler.

        Default: ``10``

    .. envvar:: VCPU_CREDIT_PER_HOUR

        Cost of running one vCPU core for one hour.

        Default: ``1``

    .. envvar:: RAM_CREDIT_PER_HOUR

        Cost of running one GB of RAM for one hour.

        Default: ``0.3``

    .. envvar:: API_KEY

        X-API-KEY to use for authentication protected endpoints.
    """

    CLOUD_GOVERNANCE_MAIL: str
    CREDITS_HISTORY_DB: str
    # named this way to match environment variable used by the influxdb docker image
    INFLUXDB_DB: str
    INFLUXDB_HOST: str
    INFLUXDB_PORT: int
    INFLUXDB_USER: str
    INFLUXDB_USER_PASSWORD: str
    MAIL_FROM: str
    MAIL_NOT_STARTTLS: bool
    MAIL_SMTP_PASSWORD: str
    MAIL_SMTP_PORT: int
    MAIL_SMTP_USER: str
    MAIL_SMTP_SERVER: str
    NOTIFICATION_TO_OVERWRITE: str
    OS_CREDITS_PERUN_LOGIN: str
    OS_CREDITS_PERUN_PASSWORD: str
    OS_CREDITS_PERUN_VO_ID: int
    OS_CREDITS_PRECISION: Decimal
    OS_CREDITS_PROJECT_WHITELIST: Optional[Set[str]]
    OS_CREDITS_WORKERS: int
    VCPU_CREDIT_PER_HOUR: Decimal
    RAM_CREDIT_PER_HOUR: Decimal
    API_KEY: str


default_config = Config(
    CLOUD_GOVERNANCE_MAIL="",
    CREDITS_HISTORY_DB="credits_history",
    INFLUXDB_DB="",
    INFLUXDB_HOST="localhost",
    INFLUXDB_PORT=8086,
    INFLUXDB_USER="",
    INFLUXDB_USER_PASSWORD="",
    MAIL_FROM="cloud@denbi.de",
    MAIL_NOT_STARTTLS=False,
    MAIL_SMTP_PASSWORD="",
    MAIL_SMTP_PORT=25,
    MAIL_SMTP_SERVER="localhost",
    MAIL_SMTP_USER="",
    NOTIFICATION_TO_OVERWRITE="",
    OS_CREDITS_PERUN_LOGIN="",
    OS_CREDITS_PERUN_PASSWORD="",
    OS_CREDITS_PERUN_VO_ID=0,
    OS_CREDITS_PRECISION=Decimal(10) ** -2,
    OS_CREDITS_PROJECT_WHITELIST=None,
    OS_CREDITS_WORKERS=10,
    VCPU_CREDIT_PER_HOUR=Decimal(1),
    RAM_CREDIT_PER_HOUR=Decimal("0.3"),
    API_KEY=""
)


def parse_config_from_environment() -> Config:
    # for environment variables that need to be processed
    PROCESSED_ENV_CONFIG: Dict[str, Any] = {}

    try:
        PROCESSED_ENV_CONFIG.update(
            {
                "OS_CREDITS_PROJECT_WHITELIST": set(
                    environ["OS_CREDITS_PROJECT_WHITELIST"].split(";")
                )
            }
        )
    except KeyError:
        # Environment variable not set, that's ok
        pass
    for bool_value in ["MAIL_NOT_STARTTLS"]:
        if bool_value in environ:
            PROCESSED_ENV_CONFIG.update({bool_value: True})

    for int_value_key in [
        "OS_CREDITS_PRECISION",
        "OS_CREDITS_WORKERS",
        "INFLUXDB_PORT",
        "OS_CREDITS_PERUN_VO_ID",
        "MAIL_SMTP_PORT",
    ]:
        try:
            int_value = int(environ[int_value_key])
            if int_value < 0:
                internal_logger.warning(
                    "Integer value (%s) must not be negative, falling back to default "
                    "value",
                    int_value_key,
                )
                del environ[int_value_key]
                continue
            PROCESSED_ENV_CONFIG.update({int_value_key: int_value})
            internal_logger.debug(f"Added {int_value_key} to procssed env")
        except KeyError:
            # Environment variable not set, that's ok
            pass
        except ValueError:
            internal_logger.warning(
                "Could not convert value of $%s('%s') to int",
                int_value_key,
                environ[int_value_key],
            )
            # since we cannot use a subset of the actual environment, see below, we have
            # to remove invalid keys from environment to make sure that if such a key is
            # looked up inside the config the chainmap does not return the unprocessed
            # value from the environment but rather the default one
            del environ[int_value_key]

    for decimal_value_key in [
        "VCPU_CREDIT_PER_HOUR",
        "RAM_CREDIT_PER_HOUR",
    ]:
        try:
            decimal_value = Decimal(environ[decimal_value_key])
            if decimal_value < 0:
                internal_logger.warning(
                    "Decimal value (%s) must not be negative, falling back to default "
                    "value",
                    decimal_value_key,
                )
                del environ[decimal_value_key]
                continue
            PROCESSED_ENV_CONFIG.update({decimal_value_key: decimal_value})
            internal_logger.debug(f"Added {decimal_value_key} to procssed env")
        except KeyError:
            # Environment variable not set, that's ok
            pass
        except ValueError:
            internal_logger.warning(
                "Could not convert value of $%s('%s') to Decimal",
                decimal_value_key,
                environ[decimal_value_key],
            )
            # since we cannot use a subset of the actual environment, see below, we have
            # to remove invalid keys from environment to make sure that if such a key is
            # looked up inside the config the chainmap does not return the unprocessed
            # value from the environment but rather the default one
            del environ[decimal_value_key]

    if "OS_CREDITS_PRECISION" in PROCESSED_ENV_CONFIG:
        PROCESSED_ENV_CONFIG["OS_CREDITS_PRECISION"] = (
            Decimal(10) ** -PROCESSED_ENV_CONFIG["OS_CREDITS_PRECISION"]
        )

    if "VCPU_CREDIT_PER_HOUR" in PROCESSED_ENV_CONFIG:
        PROCESSED_ENV_CONFIG["VCPU_CREDIT_PER_HOUR"] = (
            Decimal(PROCESSED_ENV_CONFIG["VCPU_CREDIT_PER_HOUR"])
        )

    if "RAM_CREDIT_PER_HOUR" in PROCESSED_ENV_CONFIG:
        PROCESSED_ENV_CONFIG["RAM_CREDIT_PER_HOUR"] = (
            Decimal(PROCESSED_ENV_CONFIG["RAM_CREDIT_PER_HOUR"])
        )

    # this would be the right way but makes pytest hang forever -.-'
    # use the workaround explained above and add the raw process environment to the
    # chainmap although this is not really nice :(
    # At least mypy should show an error whenever a config value not defined in
    # :class:`Config` is accessed

    # for key in Config.__annotations__:
    #    # every value which needs processing should already be present in
    #    # PROCESSED_ENV_CONFIG if set in the environment
    #    if key in PROCESSED_ENV_CONFIG:
    #        continue
    #    if key in environ:
    #        PROCESSED_ENV_CONFIG.update({key: environ[key]})
    return cast(Config, PROCESSED_ENV_CONFIG)


class _EmptyConfig(UserDict):
    """
    Used as last element inside the config chainmap. If its :func:`__getitem__` method
    is called the requested value is not available and we have to exit.
    """

    def __getitem__(self, key):
        internal_logger.exception(
            "Config value %s was requested but not known. Appending stacktrace", key
        )
        raise MissingConfigError(f"Missing value for key {key}")


config = cast(
    Config,
    # once the problem with pytest is resolved remove `environ` from this list
    ChainMap(parse_config_from_environment(), environ, default_config, _EmptyConfig()),
)
