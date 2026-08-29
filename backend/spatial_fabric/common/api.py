"""平台级最小公共 API。健康检查必须轻量、无业务副作用。"""

from __future__ import annotations

from django.db import connection
from rest_framework import permissions, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView


class LivenessView(APIView):
    """只回答 Web 进程是否存活，不访问数据库或可选 Provider。"""

    authentication_classes: list[type] = []
    permission_classes = [permissions.AllowAny]

    def get(self, request: Request) -> Response:
        return Response({"status": "ok", "service": "spatial-fabric"})


class ReadinessView(APIView):
    """当前最小就绪条件：PostgreSQL 可连接且 PostGIS 扩展可用。"""

    authentication_classes: list[type] = []
    permission_classes = [permissions.AllowAny]

    def get(self, request: Request) -> Response:
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT PostGIS_Version()")
                postgis_version = cursor.fetchone()[0]
        except Exception as exc:
            return Response(
                {"status": "not_ready", "database": "unavailable", "detail": type(exc).__name__},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response({"status": "ready", "database": "ok", "postgis": postgis_version})
