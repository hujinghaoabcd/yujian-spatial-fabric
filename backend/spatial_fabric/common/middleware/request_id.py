"""请求关联 ID 中间件。"""

from __future__ import annotations

from collections.abc import Callable

from django.http import HttpRequest, HttpResponse

from spatial_fabric.common.ids import uuid7


class RequestIdMiddleware:
    """为每个 HTTP 请求绑定可跨日志/Trace/Job 传播的 request_id。"""

    header_name = "HTTP_X_REQUEST_ID"
    response_header = "X-Request-ID"

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        supplied = request.META.get(self.header_name, "").strip()
        request.request_id = supplied[:128] if supplied else str(uuid7())  # type: ignore[attr-defined]
        response = self.get_response(request)
        response[self.response_header] = request.request_id  # type: ignore[attr-defined]
        return response
