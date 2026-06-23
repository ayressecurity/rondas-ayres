"""Vistas de la app 'rondas'. Placeholder navegable dentro de una instalacion.

(El escáner de prueba de QR vive ahora en su propia app: apps/escaner.
Rondas se usará para PROGRAMAR rondas.)
"""
from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from apps.comun.decoradores import requiere_instalacion


@login_required
@requiere_instalacion
def index(request):
    return render(request, "rondas/index.html")
