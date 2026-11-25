"""Explicit operator authentication; credentials are configured only on the server."""

import secrets
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.config import Settings, get_settings
from app.errors import BusinessError

basic = HTTPBasic(auto_error=False)


def require_admin(settings: Annotated[Settings, Depends(get_settings)],
                  credentials: Annotated[HTTPBasicCredentials | None, Depends(basic)]) -> str:
    password = settings.admin_password.get_secret_value() if settings.admin_password else ""
    if not settings.admin_username or not password:
        raise BusinessError("ADMIN_NOT_CONFIGURED", 503)
    username_ok = secrets.compare_digest((credentials.username if credentials else "").encode(),
                                         settings.admin_username.encode())
    password_ok = secrets.compare_digest((credentials.password if credentials else "").encode(), password.encode())
    if not (username_ok and password_ok):
        raise BusinessError("ADMIN_UNAUTHORIZED", 401)
    return settings.admin_username
