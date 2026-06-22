from django.urls import path

from . import views

app_name = "control_vehicular"

urlpatterns = [
    path("", views.index, name="index"),
]
