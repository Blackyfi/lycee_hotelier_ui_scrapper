"""Poll the GMAIL_USERNAME inbox for STOP/START commands and apply them.

The subject line of an unread message must contain `STOP <token>` or
`START <token>`. We deliberately do NOT scan the body: a "Reply" quotes the
original notification, whose own footer contains a literal `STOP <token>` line,
so body-matching would apply STOP on every reply.

Users must therefore *compose a new email* (or *forward* a notification) and
put the command in the subject. Each user has a unique `command_token` issued
at create-time; it's printed in the email footer so subscribers can opt-out
without the dashboard needing to be reachable from the public internet.
"""
from __future__ import annotations

import email
import imaplib
import logging
import re
import smtplib
from email.message import EmailMessage
from email.utils import formataddr, parseaddr
from threading import Lock

from ..config import get_settings
from ..db import session_scope
from ..models import User

log = logging.getLogger(__name__)

_CMD_RE = re.compile(r"\b(STOP|START)\s+([A-Za-z0-9_\-]{8,})\b", re.IGNORECASE)
_inbox_lock = Lock()


def _extract_subject(msg: email.message.Message) -> str:
    """Return the decoded Subject header (only). We intentionally ignore the body:
    replies auto-quote the original notification, whose footer already contains a
    literal `STOP <token>` line — parsing the body would cause false positives."""
    raw = msg.get("Subject") or ""
    try:
        from email.header import decode_header, make_header
        return str(make_header(decode_header(raw)))
    except Exception:
        return raw


def _send_confirmation(to_email: str, action: str, user_name: str) -> None:
    """Best-effort 1-line confirmation back to the requester."""
    settings = get_settings()
    if settings.notifications_dry_run:
        log.info("[DRY-RUN imap-confirm] to=%s action=%s", to_email, action)
        return
    if not settings.gmail_username or not settings.gmail_app_password:
        return

    app_password = "".join(settings.gmail_app_password.split())
    if action == "STOP":
        subject = "[Lycée Hôtelier] Notifications désactivées"
        body = (f"Bonjour {user_name},\n\n"
                "Vos notifications sont maintenant désactivées.\n"
                "Pour les réactiver, envoyez un nouvel email dont le sujet contient "
                "`START <votre token>`.\n")
    else:
        subject = "[Lycée Hôtelier] Notifications réactivées"
        body = (f"Bonjour {user_name},\n\n"
                "Vos notifications sont à nouveau actives.\n"
                "Pour les arrêter, envoyez un nouvel email dont le sujet contient "
                "`STOP <votre token>`.\n")

    msg = EmailMessage()
    msg["From"] = formataddr((settings.gmail_from_name, settings.gmail_username))
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(body)

    try:
        with smtplib.SMTP(settings.gmail_smtp_host, settings.gmail_smtp_port, timeout=20) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.login(settings.gmail_username, app_password)
            smtp.send_message(msg)
    except Exception:
        log.exception("imap confirmation send failed (action=%s, to=%s)", action, to_email)


def poll_inbox() -> dict:
    """Scan UNSEEN messages once. Returns counts. Safe to call from scheduler."""
    settings = get_settings()
    counts = {"scanned": 0, "matched": 0, "applied": 0, "errors": 0}

    if not settings.imap_enabled:
        return counts
    if not settings.gmail_username or not settings.gmail_app_password:
        log.info("imap poll skipped: gmail credentials not configured")
        return counts
    if not _inbox_lock.acquire(blocking=False):
        log.warning("imap poll already running, skipping this tick")
        return counts

    app_password = "".join(settings.gmail_app_password.split())
    try:
        with imaplib.IMAP4_SSL(settings.imap_host, settings.imap_port) as imap:
            imap.login(settings.gmail_username, app_password)
            imap.select(settings.imap_folder)
            typ, data = imap.search(None, "UNSEEN")
            if typ != "OK":
                return counts
            ids = data[0].split()
            counts["scanned"] = len(ids)

            for msg_id in ids:
                try:
                    typ, msg_data = imap.fetch(msg_id, "(RFC822)")
                    if typ != "OK" or not msg_data or not msg_data[0]:
                        continue
                    raw = msg_data[0][1]
                    msg = email.message_from_bytes(raw)
                    subject = _extract_subject(msg)
                    m = _CMD_RE.search(subject)
                    if not m:
                        # leave unread so a human can triage later
                        continue
                    counts["matched"] += 1
                    action = m.group(1).upper()
                    token = m.group(2)
                    from_addr = parseaddr(msg.get("From") or "")[1]
                    applied = _apply_command(action, token, from_addr)
                    if applied:
                        counts["applied"] += 1
                        # mark seen so we don't re-process
                        imap.store(msg_id, "+FLAGS", "\\Seen")
                except Exception:
                    counts["errors"] += 1
                    log.exception("error processing imap message id=%s", msg_id)
    except Exception:
        log.exception("imap poll failed")
        counts["errors"] += 1
    finally:
        _inbox_lock.release()

    return counts


def _apply_command(action: str, token: str, from_addr: str) -> bool:
    """Find the user by token, toggle enabled, send confirmation. Returns True on success."""
    with session_scope() as s:
        user = s.query(User).filter(User.command_token == token).one_or_none()
        if user is None:
            log.warning("imap command %s with unknown token=%s from=%s", action, token, from_addr)
            return False
        wanted = action == "START"
        if user.enabled == wanted:
            log.info("imap command %s for user=%s — already in target state", action, user.email)
        else:
            user.enabled = wanted
            log.info("imap command %s applied to user=%s (from=%s)", action, user.email, from_addr)
        # Reply to the from-address if present, else to the user's registered email.
        reply_to = from_addr or user.email
        _send_confirmation(reply_to, action, user.name)
        return True
