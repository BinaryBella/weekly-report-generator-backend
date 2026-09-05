"""Outbound transactional email (invite credentials) over plain SMTP.

Deliberately best-effort: nothing here ever raises past this module. If SMTP
isn't configured, or sending fails for any reason, the caller gets ``False``
back and a line in the logs - the Manager can still see and share the
temporary password shown in the UI, so a mail-server outage never blocks
inviting a team member.
"""

from __future__ import annotations

import asyncio
import logging
import smtplib
from email.message import EmailMessage
from html import escape

from app.core.config import settings

logger = logging.getLogger(__name__)


def _send_sync(*, to_email: str, subject: str, text_body: str, html_body: str) -> None:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = f"{settings.smtp_from_name} <{settings.smtp_from_email}>"
    message["To"] = to_email
    message.set_content(text_body)
    message.add_alternative(html_body, subtype="html")

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as server:
        if settings.smtp_use_tls:
            server.starttls()
        if settings.smtp_username and settings.smtp_password:
            server.login(settings.smtp_username, settings.smtp_password)
        server.send_message(message)


async def send_invite_email(
    *, to_email: str, to_name: str, temporary_password: str, role: str
) -> bool:
    """Email a newly invited team member their sign-in credentials.

    Returns ``True`` only once the SMTP send genuinely succeeds. Returns
    ``False`` (never raises) when email isn't configured or sending fails.
    """
    if not settings.email_enabled:
        logger.info("Email not configured; skipping invite email to %s", to_email)
        return False

    login_url = f"{settings.frontend_base_url.rstrip('/')}/login"
    subject = "You've been invited to Weekly Report Generator"
    text_body = (
        f"Hi {to_name},\n\n"
        f"An account has been created for you on Weekly Report Generator as a "
        f"{role}.\n\n"
        f"Email: {to_email}\n"
        f"Temporary password: {temporary_password}\n\n"
        f"Sign in and change your password as soon as possible:\n{login_url}\n"
    )
    html_body = (
        f"<p>Hi {escape(to_name)},</p>"
        f"<p>An account has been created for you on "
        f"<strong>Weekly Report Generator</strong> as a <strong>{escape(role)}</strong>.</p>"
        f"<p>Email: <strong>{escape(to_email)}</strong><br>"
        f"Temporary password: <strong>{escape(temporary_password)}</strong></p>"
        f'<p><a href="{login_url}">Sign in</a> and change your password as soon '
        f"as possible.</p>"
    )

    try:
        await asyncio.to_thread(
            _send_sync,
            to_email=to_email,
            subject=subject,
            text_body=text_body,
            html_body=html_body,
        )
    except Exception:
        logger.exception("Failed to send invite email to %s", to_email)
        return False
    return True
