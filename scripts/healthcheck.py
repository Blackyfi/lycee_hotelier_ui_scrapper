from __future__ import annotations

import os
import ssl
import urllib.request


def _unverified_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def main() -> int:
    tls_enabled = bool(os.getenv("TLS_CERT_FILE") and os.getenv("TLS_KEY_FILE"))
    scheme = "https" if tls_enabled else "http"
    url = f"{scheme}://127.0.0.1:8000/healthz"
    context = _unverified_context() if tls_enabled else None
    with urllib.request.urlopen(url, timeout=5, context=context) as response:
        return 0 if response.status == 200 else 1


if __name__ == "__main__":
    raise SystemExit(main())
