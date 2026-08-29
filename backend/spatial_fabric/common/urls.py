from django.urls import path
from rest_framework.response import Response
from rest_framework.views import APIView


class ApiRootView(APIView):
    """API v1 根信息；后续各 bounded context 通过 include 分模块挂载。"""
    def get(self, request):  # type: ignore[no-untyped-def]
        return Response({"name": "Yujian Spatial Fabric API", "version": "v1"})


urlpatterns = [path("", ApiRootView.as_view(), name="api-v1-root")]
