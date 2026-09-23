from app.core import direct_http


def test_requests_client_ignores_environment_proxies(monkeypatch):
    state = {}
    expected = object()

    class FakeSession:
        trust_env = True

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def post(self, url, **kwargs):
            state["trust_env"] = self.trust_env
            state["url"] = url
            state["kwargs"] = kwargs
            return expected

    monkeypatch.setattr(direct_http.requests, "Session", FakeSession)

    result = direct_http.post_direct("https://example.invalid/v1", timeout=3)

    assert result is expected
    assert state == {
        "trust_env": False,
        "url": "https://example.invalid/v1",
        "kwargs": {"timeout": 3},
    }


def test_urllib_client_has_empty_proxy_configuration():
    proxy_handlers = [
        handler
        for handler in direct_http._DIRECT_URL_OPENER.handlers
        if hasattr(handler, "proxies")
    ]
    # urllib omits an explicitly empty ProxyHandler from the final handler list.
    assert proxy_handlers == []
