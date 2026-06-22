from django.urls import path

from . import views

app_name = "instalaciones"

urlpatterns = [
    path("", views.index, name="index"),
    path("seleccionar/<int:instalacion_id>/", views.seleccionar, name="seleccionar"),
    path("cambiar/", views.cambiar, name="cambiar"),
]
