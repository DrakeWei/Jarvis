from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from feishu_mcp_server.config import settings


class FeishuAuthError(RuntimeError):
    pass


@dataclass
class CachedToken:
    value: str
    expires_at: float


class FeishuAuthManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cached: CachedToken | None = None

    def get_tenant_access_token(self) -> str:
        if not settings.credentials_configured():
            raise FeishuAuthError("Missing FEISHU_APP_ID or FEISHU_APP_SECRET.")

        now = time.time()
        with self._lock:
            if self._cached and now < self._cached.expires_at - settings.token_refresh_skew_seconds:
                return self._cached.value
            token, expires_in = self._fetch_token()
            self._cached = CachedToken(value=token, expires_at=now + max(expires_in, 60))
            return token

    def _fetch_token(self) -> tuple[str, int]:
        body = json.dumps(
            {
                "app_id": settings.app_id,
                "app_secret": settings.app_secret,
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{settings.api_base_url}/open-apis/auth/v3/tenant_access_token/internal",
            data=body,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise FeishuAuthError(f"Feishu token request failed: {exc.code} {detail}") from exc
        except urllib.error.URLError as exc:
            raise FeishuAuthError(f"Feishu token request failed: {exc.reason}") from exc
        except json.JSONDecodeError as exc:
            raise FeishuAuthError("Feishu token request returned invalid JSON.") from exc

        if not isinstance(payload, dict):
            raise FeishuAuthError("Feishu token request returned a malformed response.")
        if int(payload.get("code", -1)) != 0:
            raise FeishuAuthError(f"Feishu token request failed: {payload.get('msg', 'unknown error')}")
        token = str(payload.get("tenant_access_token") or "").strip()
        expires_in = int(payload.get("expire", 7200))
        if not token:
            raise FeishuAuthError("Feishu token request succeeded but no token was returned.")
        return token, expires_in


auth_manager = FeishuAuthManager()
