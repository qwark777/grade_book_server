import asyncio
import smtplib
from email.message import EmailMessage
from email.utils import formataddr

from app.core.config import settings


def _send_message(message: EmailMessage) -> None:
    with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as smtp:
        if settings.SMTP_USE_TLS:
            smtp.starttls()
        if settings.SMTP_USERNAME:
            smtp.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
        smtp.send_message(message)


async def send_password_reset_email(email: str, reset_url: str) -> None:
    if not settings.SMTP_HOST or not settings.SMTP_FROM_EMAIL:
        raise RuntimeError("SMTP is not configured")

    message = EmailMessage()
    message["Subject"] = "Восстановление пароля GradeBook"
    message["From"] = formataddr(
        (settings.SMTP_FROM_NAME, settings.SMTP_FROM_EMAIL)
    )
    message["To"] = email
    message.set_content(
        "Для установки нового пароля откройте ссылку:\n\n"
        f"{reset_url}\n\n"
        f"Ссылка действует {settings.PASSWORD_RESET_EXPIRE_MINUTES} минут. "
        "Если вы не запрашивали восстановление, проигнорируйте письмо."
    )
    await asyncio.to_thread(_send_message, message)


async def send_landing_lead_email(
    *,
    name: str | None,
    email: str,
    page_url: str | None = None,
    user_agent: str | None = None,
) -> None:
    if not settings.SMTP_HOST or not settings.SMTP_FROM_EMAIL:
        raise RuntimeError("SMTP is not configured")
    if not settings.LANDING_LEADS_EMAIL:
        raise RuntimeError("Landing leads email is not configured")

    clean_name = (name or "").strip() or "Не указано"
    clean_page_url = (page_url or "").strip() or "Не указана"
    clean_user_agent = (user_agent or "").strip() or "Не указан"

    message = EmailMessage()
    message["Subject"] = "Новая заявка с сайта GradeBook"
    message["From"] = formataddr(
        (settings.SMTP_FROM_NAME, settings.SMTP_FROM_EMAIL)
    )
    message["To"] = settings.LANDING_LEADS_EMAIL
    message["Reply-To"] = email
    message.set_content(
        "Новая заявка на демо GradeBook\n\n"
        f"Имя: {clean_name}\n"
        f"Email: {email}\n"
        f"Страница: {clean_page_url}\n"
        f"User-Agent: {clean_user_agent}\n"
    )
    await asyncio.to_thread(_send_message, message)
