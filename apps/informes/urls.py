from django.urls import path

from . import views

app_name = "informes"

urlpatterns = [
    path("rondas/", views.informe_rondas, name="rondas"),
]
