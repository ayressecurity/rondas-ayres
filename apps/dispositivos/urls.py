from django.urls import path

from . import views

app_name = "dispositivos"

urlpatterns = [
    path("", views.index, name="index"),                       # panel: QR + lista
    path("generar/", views.generar, name="generar"),           # crear secreto (1ª vez)
    path("rotar/", views.rotar, name="rotar"),                 # regenerar secreto
    path("qr.png", views.qr_imagen, name="qr_imagen"),         # PNG al vuelo (?descargar=1)
    path("imprimir/", views.imprimir, name="imprimir"),        # hoja imprimible
    path("<int:pk>/revocar/", views.revocar, name="revocar"),
    path("<int:pk>/reactivar/", views.reactivar, name="reactivar"),
]
