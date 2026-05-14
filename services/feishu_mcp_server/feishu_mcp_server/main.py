from __future__ import annotations

import uvicorn

from feishu_mcp_server.config import settings


def main() -> None:
    uvicorn.run(
        "feishu_mcp_server.mcp_server:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )


if __name__ == "__main__":
    main()
