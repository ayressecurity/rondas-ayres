"""Rutas de la API móvil. El prefijo /api/ se monta en config/urls.py."""
from django.urls import path

from apps.api import views

app_name = "api"

urlpatterns = [
    # GET /api/me — prueba del portero (token válido -> 200).
    path("me", views.me, name="me"),
    # GET /api/checkpoints/by-qr/{qr_token} — resuelve un punto por su QR.
    path("checkpoints/by-qr/<str:qr_token>", views.checkpoint_by_qr, name="checkpoint_by_qr"),
    # GET /api/rondas?mias — rondas del guardia del token.
    path("rondas", views.rondas_mias, name="rondas_mias"),
    # GET /api/notificaciones?mias — recordatorios del guardia del token.
    path("notificaciones", views.notificaciones_mias, name="notificaciones_mias"),
    # POST /api/eventos — registra una marca (escaneo) del guardia.
    path("eventos", views.crear_evento, name="crear_evento"),
    # POST /api/eventos/{id}/media — adjunta archivos a un evento del guardia.
    path("eventos/<int:evento_id>/media", views.subir_media, name="subir_media"),
    # POST /api/dispositivos/enroll — enrolamiento PÚBLICO de un teléfono (Fase 3).
    path("dispositivos/enroll", views.enroll_dispositivo, name="enroll_dispositivo"),
    # POST /api/sesion/inicio — inicio de turno del guardia (sesion_inicio).
    path("sesion/inicio", views.sesion_inicio, name="sesion_inicio"),
]
