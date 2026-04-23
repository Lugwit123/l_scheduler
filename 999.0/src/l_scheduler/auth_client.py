from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests


@dataclass(frozen=True)
class LoginResult:
    access_token: str
    token_type: str = "bearer"


def login_password(
    *,
    auth_url: str,
    username: str,
    password: str,
    nickname: str | None = None,
    timeout_seconds: float = 8.0,
) -> LoginResult:
    """
    Login via ChatRoom-style OAuth2 form endpoint.

    ChatRoom uses `OAuth2PasswordRequestFormWithNickname` (username/password + nickname optional).
    """
    data: dict[str, Any] = {
        "username": username,
        "password": password,
    }
    if nickname is not None:
        data["nickname"] = nickname

    resp = requests.post(auth_url, data=data, timeout=timeout_seconds)
    resp.raise_for_status()
    obj = resp.json()
    token = str(obj.get("access_token") or "")
    token_type = str(obj.get("token_type") or "bearer")
    if not token:
        raise ValueError(f"login response missing access_token: keys={list(obj.keys())}")
    return LoginResult(access_token=token, token_type=token_type)

