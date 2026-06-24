from django.urls import path

from . import views

app_name = "checkpoints"

urlpatterns = [
    path("", views.index, name="index"),
    path("nuevo/", views.nuevo, name="nuevo"),
    path("editar/<int:pk>/", views.editar, name="editar"),
    path("qr/<int:pk>/", views.qr, name="qr"),
    path("imprimir/", views.imprimir, name="imprimir"),
    path("configurar-qr/", views.configurar_qr, name="configurar_qr"),
    path("configurar-qr/buscar/", views.configurar_qr_buscar, name="configurar_qr_buscar"),
    path("configurar-qr/guardar/", views.configurar_qr_guardar, name="configurar_qr_guardar"),
    path("eliminar/<int:pk>/", views.eliminar, name="eliminar"),
]
