from __future__ import annotations

from contextvars import ContextVar
from logging import Filter
from logging import LogRecord
from logging import getLogger

TASK_ID: ContextVar[str] = ContextVar("TASK_ID", default="")
"""For every task context this holds the ID generated by
:func:`~os_credits.cb_client.tasks.unique_identifier`. In conjunction with
:class:`TaskIdFilter` this allows us to prefix all logging calls with the ID of the
task's context. With this approach the logging calls **do not** have to pass their
task's ID to the call (would be forgotten in most cases expectedly).
"""


class TaskIdFilter(Filter):
    """Subclass of :class:`logging.Filter` which can also be used to add attributes to
    log records.

    Is configured and applied in :data:`DEFAULT_LOGGING_CONFIG`.
    """

    def filter(self, record: LogRecord) -> bool:
        """Used to add the unique task ID stored in :attr:`TASK_ID` to the log record.

        :param record: Log record to add the task to
        :return: Whether this log message should be filtered. Always ``True`` in our
            case since we do not use the Filter to filter but rather extend.
        """
        record.task_id = TASK_ID.get()  # type: ignore
        return True


task_logger = getLogger("os_credits.tasks")
"""Responsible for logging all high-level events that occurred during the :ref:`Billing
Workflow`.
"""
internal_logger = getLogger("os_credits.internal")
"""Used by the other modules of the service for logging, such as the creation of objects
or such.
"""
requests_logger = getLogger("os_credits.requests")
"""Logs all the communication with *Perun*, should only be used for debugging purposes
since it logs **all** exchanged data.
"""
timescaledb_logger = getLogger("os_credits.timescaledb")
"""Logs all the communication with the *timescaledb*.
"""
producer_logger = getLogger("os_credits.producer")
views_logger = getLogger("os_credits.views")

DEFAULT_LOG_LEVEL = {
    "os_credits.tasks": "INFO",
    "os_credits.internal": "INFO",
    "os_credits.requests": "INFO",
    "os_credits.timescaledb": "INFO",
    "os_credits.producer": "INFO",
    "os_credits.views": "INFO"
}
"""Default logging level of all loggers.

.. todo:: Allow setting log levels with environment variables.
"""

DEFAULT_LOGGING_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "task_handler": {
            "format": "[%(levelname)s] %(asctime)s %(name)s %(funcName)s:%(lineno)d: [%(task_id)s] %(message)s"
        },
        "simple_handler": {
            "format": "[%(levelname)s] %(asctime)s %(name)s %(funcName)s:%(lineno)d: %(message)s"
        },
    },
    "filters": {"task_id_filter": {"()": "os_credits.log.TaskIdFilter"}},
    "handlers": {
        "with_task_id": {
            "class": "logging.StreamHandler",
            "level": "DEBUG",
            "stream": "ext://sys.stdout",
            "formatter": "task_handler",
        },
        "simple": {
            "class": "logging.StreamHandler",
            "level": "DEBUG",
            "stream": "ext://sys.stdout",
            "formatter": "simple_handler",
        },
    },
    "loggers": {
        "os_credits.tasks": {
            "level": DEFAULT_LOG_LEVEL["os_credits.tasks"],
            "handlers": ["with_task_id"],
            "filters": ["task_id_filter"],
        },
        "os_credits.internal": {
            "level": DEFAULT_LOG_LEVEL["os_credits.internal"],
            "handlers": ["simple"],
        },
        "os_credits.requests": {
            "level": DEFAULT_LOG_LEVEL["os_credits.requests"],
            "handlers": ["simple"],
        },
        "os_credits.timescaledb": {
            "level": DEFAULT_LOG_LEVEL["os_credits.timescaledb"],
            "handlers": ["simple"],
        },
        "os_credits.producer": {
            "level": DEFAULT_LOG_LEVEL["os_credits.producer"],
            "handlers": ["simple"],
        },
        "os_credits.views": {
            "level": DEFAULT_LOG_LEVEL["os_credits.views"],
            "handlers": ["simple"],
        },
    },
}
"""Passed to :func:`~logging.config.dictConfig` in at :ref:`Startup`.
"""
