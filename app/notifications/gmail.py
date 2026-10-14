from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path
from typing import Protocol

from ..config import get_settings

log = logging.getLogger(__name__)


class EmailContent(Protocol):
    @property
    def subject(self) -> str: ...
    def text(self) -> str: ...
    def html(self) -> str: ...


def send_email(
    to_email: str,
    payload: EmailContent,
    inline_gifs: dict[str, Path] | None = None,
) -> tuple[bool, str | None]:
    """Send payload as text + HTML. inline_gifs maps Content-ID → file, referenced as cid:<id> in the HTML."""
    settings = get_settings()
    if settings.notifications_dry_run:
        log.info("[DRY-RUN email] to=%s subject=%s", to_email, payload.subject)
        return True, None
    if not settings.gmail_username or not settings.gmail_app_password:
        return (
            False,
            "Gmail credentials not configured (GMAIL_USERNAME / GMAIL_APP_PASSWORD)",
        )

    # Google displays app passwords as "xxxx xxxx xxxx xxxx" for readability; SMTP
    # wants the 16 chars contiguous. Strip all whitespace so either format works.
    app_password = "".join(settings.gmail_app_password.split())

    msg = EmailMessage()
    msg["From"] = formataddr((settings.gmail_from_name, settings.gmail_username))
    msg["To"] = to_email
    msg["Subject"] = payload.subject
    msg.set_content(payload.text())
    msg.add_alternative(payload.html(), subtype="html")
    if inline_gifs:
        html_part = msg.get_payload()[1]
        for cid, path in inline_gifs.items():
            html_part.add_related(
                path.read_bytes(), maintype="image", subtype="gif", cid=f"<{cid}>", filename=path.name
            )

    try:
        with smtplib.SMTP(settings.gmail_smtp_host, settings.gmail_smtp_port, timeout=20) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.login(settings.gmail_username, app_password)
            smtp.send_message(msg)
        return True, None
    except Exception as e:
        log.exception("gmail send failed")
        return False, str(e)
