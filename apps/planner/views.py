from rest_framework.response import Response
from rest_framework.views import APIView

from apps.planner.serializers import PlanInputSerializer
from apps.planner.services import PlanError, plan_trip


class PlanView(APIView):
    def post(self, request):
        serializer = PlanInputSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=400)
        data = serializer.validated_data
        try:
            plan = plan_trip(
                origin=data["origin"],
                destination=data["destination"],
                departure=data["departure"],
                load_lbs=data["load_lbs"],
                sample_miles=data["sample_miles"],
            )
        except PlanError as exc:
            return Response({"error": exc.message}, status=exc.status)
        return Response(plan)
