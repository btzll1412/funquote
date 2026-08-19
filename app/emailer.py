"""Outbound email (invites, password resets) over standard SMTP.

Without SMTP configured the app still works: emails are printed to the
server log ("console mode") and invite links are shown to the admin in the
UI so they can be passed along manually. Reset links are never shown in the
UI — only emailed or logged.
"""

import smtplib
from email.message import EmailMessage

from fastapi import Request

from app import config


def smtp_configured() -> bool:
    return bool(config.SMTP_HOST)


def external_url(request: Request, path: str) -> str:
    base = config.APP_BASE_URL or str(request.base_url)
    return base.rstrip("/") + path


def send_email(to: str, subject: str, body: str) -> tuple[bool, str]:
    """Returns (sent, detail). Never raises."""
    if not smtp_configured():
        print(f"[email — console mode, SMTP not configured]\n"
              f"To: {to}\nSubject: {subject}\n{body}\n[end email]")
        return False, "SMTP is not configured on this server"
    try:
        msg = EmailMessage()
        msg["From"] = config.SMTP_FROM or config.SMTP_USERNAME
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body)
        if config.SMTP_SECURITY == "ssl":
            server = smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORT, timeout=20)
        else:
            server = smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=20)
            if config.SMTP_SECURITY == "starttls":
                server.starttls()
        if config.SMTP_USERNAME:
            server.login(config.SMTP_USERNAME, config.SMTP_PASSWORD)
        server.send_message(msg)
        server.quit()
        return True, "sent"
    except Exception as e:  # noqa: BLE001 — any SMTP failure is non-fatal
        return False, f"email could not be sent: {e}"


def invite_email_body(org_name: str, inviter_name: str, link: str) -> str:
    return (
        f"{inviter_name} has invited you to join {org_name} on FunQuote.\n\n"
        f"To accept and create your account:\n"
        f"1. Open this link: {link}\n"
        f"2. Choose a password (at least 8 characters).\n"
        f"3. You'll be signed in and ready to work.\n\n"
        f"This invitation expires in 7 days. If you weren't expecting it, "
        f"you can ignore this email."
    )


def reset_email_body(name: str, link: str) -> str:
    return (
        f"Hi {name},\n\n"
        f"A password reset was requested for your FunQuote account.\n\n"
        f"To set a new password:\n"
        f"1. Open this link: {link}\n"
        f"2. Enter your new password (at least 8 characters).\n"
        f"3. Log in with the new password.\n\n"
        f"The link expires in 2 hours and can be used once. If you didn't "
        f"request this, you can ignore this email — your password is unchanged."
    )
