"""Rutas de la API móvil. El prefijo /api/ se monta en config/urls.py."""
from django.urls import path

from apps.api import views

app_name = "api"

urlpatterns = [
    # GET /api/me — prueba del portero (token válido -> 200).
    path("me", views.me, name="me"),
]
