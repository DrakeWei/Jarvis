from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from feishu_mcp_server.auth import FeishuAuthError, auth_manager
from feishu_mcp_server.config import settings
from feishu_mcp_server.tls import build_ssl_context


class FeishuAPIError(RuntimeError):
    pass


@dataclass(frozen=True)
class FeishuAuthStatus:
    credentials_configured: bool
    token_ready: bool
    detail: str


class FeishuClient:
    def __init__(self) -> None:
        self._ssl_context = build_ssl_context()

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

    def convert_markdown_to_blocks(self, content: str) -> dict[str, Any]:
        body = {
            "content_type": "markdown",
            "content": content,
        }
        return self._request("POST", settings.doc_convert_path, body=body)

    def create_nested_blocks(
        self,
        *,
        document_id: str,
        block_id: str,
        children_id: list[str],
        descendants: list[dict[str, Any]],
        index: int = -1,
    ) -> dict[str, Any]:
        body = {
            "index": index,
            "children_id": children_id,
            "descendants": descendants,
        }
        path = settings.doc_create_nested_blocks_path_template.format(
            document_id=document_id,
            block_id=block_id,
        )
        return self._request("POST", path, body=body)

    def update_block(self, *, document_id: str, block_id: str, block_payload: dict[str, Any]) -> dict[str, Any]:
        path = settings.doc_update_block_path_template.format(document_id=document_id, block_id=block_id)
        return self._request("PATCH", path, body=block_payload)

    def delete_child_range(
        self,
        *,
        document_id: str,
        block_id: str,
        start_index: int,
        end_index: int,
    ) -> dict[str, Any]:
        body = {
            "start_index": start_index,
            "end_index": end_index,
        }
        path = settings.doc_delete_children_path_template.format(document_id=document_id, block_id=block_id)
        return self._request("DELETE", path, body=body)

    def add_permission_members(
        self,
        *,
        token: str,
        members: list[dict[str, Any]],
        file_type: str = "docx",
    ) -> dict[str, Any]:
        path = settings.doc_permission_members_batch_create_path_template.format(token=token)
        body = {"members": members}
        query = {"type": file_type}
        return self._request("POST", path, body=body, query=query)

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
            with urllib.request.urlopen(request, timeout=20, context=self._ssl_context) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise FeishuAPIError(f"Feishu API request failed: {exc.code} {detail}") from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, ssl.SSLError):
                raise FeishuAPIError(
                    "Feishu TLS verification failed. Set FEISHU_CA_BUNDLE or SSL_CERT_FILE to your trusted CA bundle."
                ) from exc
            raise FeishuAPIError(f"Feishu API request failed: {exc.reason}") from exc
        except json.JSONDecodeError as exc:
            raise FeishuAPIError("Feishu API returned invalid JSON.") from exc

        if not isinstance(payload, dict):
            raise FeishuAPIError("Feishu API returned a malformed response.")
        if int(payload.get("code", -1)) != 0:
            raise FeishuAPIError(f"Feishu API request failed: {payload.get('msg', 'unknown error')}")
        return payload


feishu_client = FeishuClient()
