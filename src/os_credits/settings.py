"""All settings can be set/overwritten by environment variables of the same name. They
are set when the :mod:`os_credits.settings` module is loaded.

The settings can be accessed via the :attr:`config` dictionary which is a
:class:`collections.ChainMap` containing the parsed and processed environment variables,
the default config values and a special dictionary :class:`_EmptyConfig` whose only
purpose is to log any access to non existing settings and raise a
:exc:`~os_credits.exceptions.MissingConfigError`.
"""

from __future__ import annotations

import json
from collections import ChainMap
from collections import UserDict
from decimal import Decimal
from os import environ
from typing import Any, TypedDict
from typing import Dict
from typing import Optional
from typing import Set
from typing import cast

from .log import internal_logger


class Config(TypedDict):
    """Used to keep track of all available settings and allows the type checker to infer
    the types of individual keys/settings.

    .. envvar:: CLOUD_GOVERNANCE_MAIL

        Mail address of the *de.NBI Cloud Governance*. Entered as ``Cc`` when sending
        certain :ref:`Notifications` such as warnings about credit usage.

    .. envvar:: CREDITS_HISTORY_DB

        Name of the database inside the TimescaleDB in which to store the
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

    # named this way to match environment variable used by the timescaledb docker image
    POSTGRES_DB: str
    POSTGRES_HOST: str
    POSTGRES_PORT: int
    POSTGRES_USER: str
    POSTGRES_PASSWORD: str
    OS_CREDITS_PROJECT_WHITELIST: Optional[Set[str]]
    OS_CREDITS_WORKERS: int
    METRICS_TO_BILL: Dict
    API_KEY: str
    API_CONTACT_KEY: str
    API_CONTACT_BASE_URL: str
    MAIL_CONTACT_URL: str
    ENDPOINTS_ONLY: bool
    OS_CREDITS_PRECISION: str


default_config = Config(
    POSTGRES_DB="credits_db",
    POSTGRES_HOST="localhost",
    POSTGRES_PORT=5432,
    POSTGRES_USER="postgres",
    POSTGRES_PASSWORD="password",
    OS_CREDITS_PROJECT_WHITELIST=None,
    OS_CREDITS_WORKERS=10,
    API_KEY="",
    API_CONTACT_KEY="",
    METRICS_TO_BILL={},
    API_CONTACT_BASE_URL="",
    MAIL_CONTACT_URL="",
    ENDPOINTS_ONLY=False,
    OS_CREDITS_PRECISION="0.01"
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

    try:
        PROCESSED_ENV_CONFIG.update({
            "METRICS_TO_BILL": json.loads(environ["METRICS_TO_BILL"])
        })
    except KeyError:
        pass

    for int_value_key in [
        "OS_CREDITS_WORKERS",
        "POSTGRES_PORT"
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
        raise f"Missing value for key {key}"


config = cast(
    Config,
    # once the problem with pytest is resolved remove `environ` from this list
    ChainMap(parse_config_from_environment(), environ, default_config, _EmptyConfig()),
)
