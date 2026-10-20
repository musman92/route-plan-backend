from django.urls import path

from apps.planner.views import PlanView

urlpatterns = [
    path("plan/", PlanView.as_view(), name="plan"),
]
