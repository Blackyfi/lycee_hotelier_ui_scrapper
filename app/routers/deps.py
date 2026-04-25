from __future__ import annotations

from fastapi import Depends, Header, HTTPException, status

from ..config import Settings, get_settings


def require_admin(
    settings: Settings = Depends(get_settings),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> None:
    if not settings.admin_token or settings.admin_token == "change-me-please":
        # Allow local dev with default token, but warn loudly via header check anyway.
        if x_admin_token != settings.admin_token:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid admin token")
        return
    if x_admin_token != settings.admin_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid admin token")
