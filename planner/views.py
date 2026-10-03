from urllib.parse import urlencode

from django.shortcuts import render
from django.urls import reverse
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import RouteRequestSerializer
from .services.exceptions import PlannerError
from .services.planner import plan_trip


def home_view(request):
    """Landing page with a simple form and links to the API."""
    return render(request, "planner/home.html")


class RoutePlanView(APIView):
    """
    Plan a fuel-optimized route between two US locations.

    GET  /api/route/?start=Dallas, TX&finish=Chicago, IL
    POST /api/route/  {"start": "Dallas, TX", "finish": "Chicago, IL"}
    """

    def get(self, request):
        return self._plan(request, request.query_params)

    def post(self, request):
        return self._plan(request, request.data)

    def _plan(self, request, data):
        serializer = RouteRequestSerializer(data=data)
        serializer.is_valid(raise_exception=True)
        start, finish = serializer.validated_data["start"], serializer.validated_data["finish"]

        try:
            plan = plan_trip(start, finish)
        except PlannerError as exc:
            return Response({"error": exc.code, "detail": exc.message}, status=exc.status_code)

        map_url = request.build_absolute_uri(
            f"{reverse('route-map')}?{urlencode({'start': start, 'finish': finish})}"
        )
        return Response({"map_url": map_url, **plan}, status=status.HTTP_200_OK)


def route_map_view(request):
    """HTML page rendering the planned route and fuel stops on an interactive Leaflet map."""
    serializer = RouteRequestSerializer(data=request.GET)
    if not serializer.is_valid():
        return render(request, "planner/map.html", {"error": "Provide both 'start' and 'finish' query parameters."}, status=400)

    try:
        plan = plan_trip(serializer.validated_data["start"], serializer.validated_data["finish"])
    except PlannerError as exc:
        return render(request, "planner/map.html", {"error": exc.message}, status=exc.status_code)

    return render(request, "planner/map.html", {"plan": plan})
