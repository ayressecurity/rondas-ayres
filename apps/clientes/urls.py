from django.urls import path

from . import views

app_name = "clientes"

urlpatterns = [
    path("", views.index, name="index"),
    path("seleccionar/<int:cliente_id>/", views.seleccionar, name="seleccionar"),
    path("cambiar/", views.cambiar, name="cambiar"),
]
