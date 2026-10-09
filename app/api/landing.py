import logging
import re

from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.services.email_service import send_landing_lead_email

router = APIRouter(prefix="/landing")
logger = logging.getLogger(__name__)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class LandingLeadRequest(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    email: str = Field(..., min_length=3, max_length=320)
    page_url: str | None = Field(default=None, max_length=1024)


@router.post("/leads", status_code=status.HTTP_202_ACCEPTED)
async def create_landing_lead(
    payload: LandingLeadRequest,
    request: Request,
    user_agent: str | None = Header(default=None),
) -> dict:
    email = payload.email.strip().lower()
    if not EMAIL_RE.fullmatch(email):
        raise HTTPException(status_code=400, detail="Некорректный email")

    name = (payload.name or "").strip() or None
    page_url = (payload.page_url or str(request.headers.get("referer") or "")).strip() or None

    try:
        await send_landing_lead_email(
            name=name,
            email=email,
            page_url=page_url,
            user_agent=user_agent,
        )
    except Exception:
        logger.exception("Could not send landing lead email")
        raise HTTPException(
            status_code=503,
            detail="Не удалось отправить заявку. Попробуйте позже.",
        ) from None

    return {"ok": True}
