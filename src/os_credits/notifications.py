"""
Notifications are modeled as exceptions so you are able to raise them whenever necessary
and don't have to worry about stopping further execution. All notifications are based on
:class:`EmailNotificationBase`. See :class:`HalfOfCreditsLeft` for a working example.
"""
from __future__ import annotations

from asyncio import AbstractEventLoop
from asyncio import get_event_loop
from email.mime.text import MIMEText
from enum import Enum
from enum import auto
from string import Template
from typing import ClassVar
from typing import Dict
from typing import Optional
from typing import Set
from typing import Union

from aiosmtplib import SMTP

from os_credits.exceptions import BrokenTemplateError
from os_credits.exceptions import MissingTemplateError
from os_credits.exceptions import MissingToError
from os_credits.log import internal_logger
from os_credits.perun.group import Group
from os_credits.settings import config


class EmailRecipient(Enum):
    """Defines placeholder values to use inside ``to``, ``cc``, ``bcc`` of Notification
    classes which will replaced dynamically when the message is constructed.
    """

    CLOUD_GOVERNANCE = auto()
    """Will be replaced with the value of ``CLOUD_GOVERNANCE_MAIL``, see :ref:`Settings`
    """

    PROJECT_MAINTAINERS = auto()
    """Will be replaced with the ``ToEmail`` addresses of this project stored inside
    Perun
    """


EmailRecipientType = Union[str, EmailRecipient]


class EmailNotificationBase(Exception):
    """Base class of all exceptions whose cause should not only be logged but also send
    to the Cloud Governance, the Group maintainers and/or other recipients.

    Subclasses are checked at creation time to make sure that we can always send
    notifications and do not fail due to broken templates or missing ``To``.
    """

    to: ClassVar[Set[EmailRecipientType]]
    """Set of recipients which will be set as ``To``. Must be set by subclasses,
    otherwise a :exc:`~os_credits.exceptions.MissingToError` is raised."""

    cc: ClassVar[Set[EmailRecipientType]] = set()
    """Set of recipients which will be set as ``Cc``."""

    bcc: ClassVar[Set[EmailRecipientType]] = set()
    """Set of recipients which will be set as ``Bcc``."""

    _subject: ClassVar[Template]
    """:class:`string.Template` object created at class creatiom from the content of
    :attr:`subject_template`."""

    subject_template: ClassVar[str]
    """String which will be parsed as :class:`string.Template` and used as the subject
    of the mail. See :attr:`custom_placeholders` and :func:`construct_message` to know
    which placeholders inside the templates are supported and how they can be
    customized."""

    _body: ClassVar[Template]
    """:class:`string.Template` object created on class instantiation from the content
    of :attr:`body_template`."""

    body_template: ClassVar[str]
    """String which will be parsed as :class:`string.Template` and used as the body of
    the mail. See :attr:`custom_placeholders` and :func:`construct_message` to know
    which placeholders inside the templates are supported and how they can be
    customized."""

    custom_placeholders: Dict[str, str] = {}
    """Custom mapping of additional placeholders and values which can be used inside
    :attr:`body_template` and :attr:`subject_template` templates. Intended to be used by
    subclasses which can add values either as class variable or at runtime in their
    constructor.
    See :func:`construct_message` for default mappings provided by the base class whose
    values are shadowed by values defined in this attribute.
    """

    def __init_subclass__(cls) -> None:
        """Automatically constructs a template from the body provided by the subclass.

        Before construction whitespace at beginning and end of the body is removed.

        :raises ValueError: In case any template construction fails or the class defines
        no `To`.
        """
        if "body_template" not in dir(cls) or not cls.body_template.strip():
            raise MissingTemplateError(
                f"Body template of {cls.__name__} is not defined or empty."
            )
        if "subject_template" not in dir(cls) or not cls.subject_template.strip():
            raise MissingTemplateError(
                f"Subject template of {cls.__name__} is not defined or empty."
            )
        if "to" not in dir(cls) or not cls.to:
            raise MissingToError(
                f"{cls.__name__} does any define any ``To`` recipients."
            )
        cls._body = Template(cls.body_template.strip())
        cls._subject = Template(cls.subject_template.strip())

    def __init__(self, group: Group, message: str) -> None:
        self.group = group
        self.message = message

    def __str__(self) -> str:
        return f"{type(self)}@{self.group.name}"

    def construct_message(self) -> MIMEText:
        """Constructs a :class:`~email.mime.text.MIMEText` object from the
        notification's attributes.

        The recipient placeholders are resolved, body and subject templates are rendered
        with the following default placeholders:

        ``project``
            Name of the Project as stored in Perun.
        ``credits_used``
            The current value of :class:`~os_credits.perun.attributes.DenbiCreditsUsed`.
        ``credits_granted``
            The current value of
            :class:`~os_credits.perun.attributes.DenbiCreditsGranted`.

        Subclasses are advised to add their own placeholders to
        :attr:`custom_placeholders` instead of overwriting this method. If any
        placeholder of a template cannot be resolved it will be left in place to ensure
        that the message can be constructed and sent.
        """

        placeholders = {
            "project": self.group.name,
            "credits_used": str(self.group.credits_used.value),
            "credits_granted": str(self.group.credits_granted.value),
            **self.custom_placeholders,
        }
        try:
            rendered_subject = self._subject.substitute(placeholders)
        except KeyError as e:
            internal_logger.error(
                "Subject of Notification %s contains unknown placeholder %s. Sending "
                "partially unformatted mail.",
                type(self).__name__,
                e,
            )
            rendered_subject = self._subject.safe_substitute(placeholders)
        except ValueError as e:
            internal_logger.error(
                "Subject of Notification %s contains invalid placeholder %s.",
                type(self).__name__,
                e,
            )
            raise BrokenTemplateError(f"Subject of Notification {type(self).__name__}")
        try:
            rendered_body = self._body.substitute(placeholders)
        except KeyError as e:
            internal_logger.error(
                "Body of Notification %s contains unknown placeholder %s. Sending "
                "partially unformatted mail.",
                type(self).__name__,
                e,
            )
            rendered_body = self._body.safe_substitute(placeholders)
        except ValueError as e:
            internal_logger.error(
                "Body of Notification %s contains invalid placeholder %s.",
                type(self).__name__,
                e,
            )
            raise BrokenTemplateError(f"Body of Notification {type(self).__name__}")
        message = MIMEText(rendered_body)
        message["Subject"] = rendered_subject
        message["From"] = config["MAIL_FROM"]
        if config["NOTIFICATION_TO_OVERWRITE"].strip():
            internal_logger.info(
                "Applying `NOTIFICATION_TO_OVERWRITE` setting to notification `%s`",
                self,
            )
            message["To"] = config["NOTIFICATION_TO_OVERWRITE"]
        else:
            message["To"] = self._resolve_recipient_placeholders(self.to)
            message["Cc"] = self._resolve_recipient_placeholders(self.cc)
            message["Bcc"] = self._resolve_recipient_placeholders(self.bcc)
        internal_logger.debug(
            "Recipients of notification `%s`: To=%s, Cc=%s, Bcc=%s",
            self,
            message["To"],
            message["Cc"],
            message["Bcc"],
        )

        return message

    def _resolve_recipient_placeholders(
        self, recipient_placeholders: Set[EmailRecipientType]
    ) -> str:
        recipients = set()
        for r in recipient_placeholders:
            if r is EmailRecipient.CLOUD_GOVERNANCE:
                recipients.add(config["CLOUD_GOVERNANCE_MAIL"])
            elif r is EmailRecipient.PROJECT_MAINTAINERS:
                for mail in self.group.email.value:
                    recipients.add(mail)
            elif isinstance(r, str):
                recipients.add(r)
        return ",".join(recipients)


class HalfOfCreditsLeft(EmailNotificationBase):
    subject_template = "50% Credits left for Project ${project}"
    to = {EmailRecipient.PROJECT_MAINTAINERS}
    cc = {EmailRecipient.CLOUD_GOVERNANCE}
    body_template = """
Dear Project Maintainer,

Your Project ${project} in the de.NBI Cloud has used $credits_used Cloud Credits of its granted $credits_granted Cloud Credits 
and therefore less than 50% of its granted Cloud Credits left. 
To view a history of your credits please login at the Cloud Portal under https://cloud.denbi.de/portal. 

For more information about Cloud Credits, its calculation and how to request more Cloud Credits please visit: 
 - https://cloud.denbi.de/wiki/portal/credits/

We also offer a Cloud Credits calculator which you may use to test different flavor/lifetime/CCs configurations which you will find at:
 - https://cloud.denbi.de/creditscalculator/

Have a nice day,
Your de.NBI Cloud Governance

"""

    def __init__(self, group: Group) -> None:
        super().__init__(
            group,
            message=f"Group {group.name} has only 50% of their credits left. Sending "
            "notification.",
        )
        self.group = group


async def send_notification(
    notification: EmailNotificationBase, loop: Optional[AbstractEventLoop] = None
) -> None:
    loop = loop or get_event_loop()
    async with SMTP(
        hostname=config["MAIL_SMTP_SERVER"], port=config["MAIL_SMTP_PORT"], loop=loop
    ) as smtp:
        if not config["MAIL_NOT_STARTTLS"]:
            internal_logger.debug("Not connecting via STARTTLS as requested")
            await smtp.starttls()
        if config["MAIL_SMTP_USER"] and config["MAIL_SMTP_PASSWORD"]:
            internal_logger.debug("Authenticating against smtp server")
            await smtp.login(config["MAIL_SMTP_USER"], config["MAIL_SMTP_PASSWORD"])
        else:
            internal_logger.debug(
                "Not authenticating against smtp server since neither user and/nor "
                "password are specified."
            )
        await smtp.send_message(notification.construct_message())
