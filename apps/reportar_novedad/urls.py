from django.urls import path

from . import views

app_name = "reportar_novedad"

urlpatterns = [
    path("", views.index, name="index"),
]
