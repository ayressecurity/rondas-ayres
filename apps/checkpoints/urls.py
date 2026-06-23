from django.urls import path

from . import views

app_name = "checkpoints"

urlpatterns = [
    path("", views.index, name="index"),
    path("nuevo/", views.nuevo, name="nuevo"),
    path("editar/<int:pk>/", views.editar, name="editar"),
    path("qr/<int:pk>/", views.qr, name="qr"),
    path("eliminar/<int:pk>/", views.eliminar, name="eliminar"),
]
