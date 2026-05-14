from __future__ import annotations

import os
import ssl


def build_ssl_context() -> ssl.SSLContext:
    cafile = os.getenv("FEISHU_CA_BUNDLE", "").strip() or os.getenv("SSL_CERT_FILE", "").strip()
    if not cafile:
        try:
            import certifi

            cafile = certifi.where()
        except ImportError:
            cafile = ""
    return ssl.create_default_context(cafile=cafile or None)
