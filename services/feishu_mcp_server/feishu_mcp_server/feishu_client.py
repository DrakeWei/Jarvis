from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from feishu_mcp_server.auth import FeishuAuthError, auth_manager
from feishu_mcp_server.config import settings


class FeishuAPIError(RuntimeError):
    pass


@dataclass(frozen=True)
class FeishuAuthStatus:
    credentials_configured: bool
    token_ready: bool
    detail: str


class FeishuClient:
    def auth_status(self) -> FeishuAuthStatus:
        try:
            token = auth_manager.get_tenant_access_token()
        except Exception as exc:
            return FeishuAuthStatus(
                credentials_configured=settings.credentials_configured(),
                token_ready=False,
                detail=str(exc),
            )
        return FeishuAuthStatus(
            credentials_configured=settings.credentials_configured(),
            token_ready=bool(token),
            detail="ok",
        )

    def create_doc(self, *, title: str, folder_token: str = "") -> dict[str, Any]:
        body: dict[str, Any] = {"title": title}
        if folder_token:
            body["folder_token"] = folder_token
        return self._request("POST", "/open-apis/docx/v1/documents", body=body)

    def get_doc(self, document_id: str) -> dict[str, Any]:
        return self._request("GET", f"/open-apis/docx/v1/documents/{document_id}")

    def list_doc_blocks(self, document_id: str, *, page_token: str = "", page_size: int = 500) -> dict[str, Any]:
        query: dict[str, Any] = {"page_size": page_size}
        if page_token:
            query["page_token"] = page_token
        return self._request("GET", f"/open-apis/docx/v1/documents/{document_id}/blocks", query=query)

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            access_token = auth_manager.get_tenant_access_token()
        except FeishuAuthError as exc:
            raise FeishuAPIError(str(exc)) from exc

        url = f"{settings.api_base_url}{path}"
        if query:
            filtered = {key: value for key, value in query.items() if value not in ("", None)}
            if filtered:
                url = f"{url}?{urllib.parse.urlencode(filtered)}"

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=utf-8",
        }
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise FeishuAPIError(f"Feishu API request failed: {exc.code} {detail}") from exc
        except urllib.error.URLError as exc:
            raise FeishuAPIError(f"Feishu API request failed: {exc.reason}") from exc
        except json.JSONDecodeError as exc:
            raise FeishuAPIError("Feishu API returned invalid JSON.") from exc

        if not isinstance(payload, dict):
            raise FeishuAPIError("Feishu API returned a malformed response.")
        if int(payload.get("code", -1)) != 0:
            raise FeishuAPIError(f"Feishu API request failed: {payload.get('msg', 'unknown error')}")
        return payload


feishu_client = FeishuClient()
