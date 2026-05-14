from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    app_name: str = "Feishu MCP Server"
    app_version: str = "0.1.0"
    protocol_version: str = "2025-03-26"
    host: str = os.getenv("FEISHU_MCP_HOST", "127.0.0.1")
    port: int = int(os.getenv("FEISHU_MCP_PORT", "8765"))
    bearer_token: str = os.getenv("FEISHU_MCP_BEARER_TOKEN", "").strip()
    app_id: str = os.getenv("FEISHU_APP_ID", "").strip()
    app_secret: str = os.getenv("FEISHU_APP_SECRET", "").strip()
    api_base_url: str = os.getenv("FEISHU_API_BASE_URL", "https://open.feishu.cn").rstrip("/")
    doc_base_url: str = os.getenv("FEISHU_DOC_BASE_URL", "https://feishu.cn/docx").rstrip("/")
    doc_convert_path: str = os.getenv("FEISHU_DOC_CONVERT_PATH", "/open-apis/docx/v1/documents/blocks/convert").strip()
    doc_create_nested_blocks_path_template: str = os.getenv(
        "FEISHU_DOC_CREATE_NESTED_BLOCKS_PATH_TEMPLATE",
        "/open-apis/docx/v1/documents/{document_id}/blocks/{block_id}/descendant",
    ).strip()
    doc_update_block_path_template: str = os.getenv(
        "FEISHU_DOC_UPDATE_BLOCK_PATH_TEMPLATE",
        "/open-apis/docx/v1/documents/{document_id}/blocks/{block_id}",
    ).strip()
    doc_delete_children_path_template: str = os.getenv(
        "FEISHU_DOC_DELETE_CHILDREN_PATH_TEMPLATE",
        "/open-apis/docx/v1/documents/{document_id}/blocks/{block_id}/children/batch_delete",
    ).strip()
    token_refresh_skew_seconds: int = int(os.getenv("FEISHU_TOKEN_REFRESH_SKEW_SECONDS", "1800"))
    max_retries: int = int(os.getenv("FEISHU_MAX_RETRIES", "3"))

    def credentials_configured(self) -> bool:
        return bool(self.app_id and self.app_secret)


settings = Settings()
