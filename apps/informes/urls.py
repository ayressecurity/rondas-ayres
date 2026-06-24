from django.urls import path

from . import views

app_name = "informes"

urlpatterns = [
    path("rondas/", views.informe_rondas, name="rondas"),
    path("rondas/excel/", views.exportar_rondas, name="exportar_rondas"),
    path("novedades/", views.informe_novedades, name="novedades"),
]
