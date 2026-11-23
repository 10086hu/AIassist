from __future__ import annotations

import urllib.request
from typing import Any

import requests


_DIRECT_URL_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def urlopen_direct(request: urllib.request.Request, *, timeout: int):
    """Open an HTTP request without inheriting Windows or environment proxies."""
    return _DIRECT_URL_OPENER.open(request, timeout=timeout)


def post_direct(url: str, **kwargs: Any) -> requests.Response:
    """POST with requests while ignoring Windows and environment proxy settings."""
    with requests.Session() as session:
        session.trust_env = False
        return session.post(url, **kwargs)
