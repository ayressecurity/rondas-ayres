from django.urls import path

from . import views

app_name = "control_vehicular"

urlpatterns = [
    path("", views.index, name="index"),          # lista
    path("nuevo/", views.nuevo, name="nuevo"),     # alta (réplica del Google Form)
]
