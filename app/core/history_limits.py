"""Bound transfer request bodies before JSON/base64 decoding."""
from starlette.responses import JSONResponse


class HistoryBodyLimitMiddleware:
    def __init__(self, app, max_bytes=50 * 1024 * 1024):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if (scope["type"] != "http" or scope.get("method") not in {"POST", "PUT", "PATCH"}
                or not path.startswith(("/api/history/", "/api/admin/history/"))):
            return await self.app(scope, receive, send)
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > self.max_bytes:
                response = JSONResponse({"detail": "传输请求超过50 MiB，请按审查批次拆分"}, status_code=413)
                return await response(scope, receive, send)
            if not message.get("more_body", False):
                break
        sent = False

        async def replay():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)
