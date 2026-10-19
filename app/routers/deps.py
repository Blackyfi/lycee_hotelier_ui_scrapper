from __future__ import annotations

import secrets

from fastapi import Depends, Header, HTTPException, status

from ..config import Settings, get_settings


def require_admin(
    settings: Settings = Depends(get_settings),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> None:
    if not settings.admin_token:
        # Refuse rather than accidentally match an empty token against the Header default (None).
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="admin token not configured",
        )
    if not x_admin_token or not secrets.compare_digest(x_admin_token.encode(), settings.admin_token.encode()):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid admin token")
