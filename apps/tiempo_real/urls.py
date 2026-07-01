from django.urls import path

from . import views

app_name = "tiempo_real"

urlpatterns = [
    path("", views.index, name="index"),        # página (render inicial server-side)
    path("data/", views.data, name="data"),      # JSON para el auto-refresco (AJAX 2s)
    path("comentar/", views.comentar, name="comentar"),  # POST: guarda/edita comentario_central
]
