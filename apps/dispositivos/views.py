"""Vistas de la app 'dispositivos'. Placeholder navegable dentro de una instalacion."""
from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from apps.comun.decoradores import requiere_instalacion


@login_required
@requiere_instalacion
def index(request):
    return render(request, "dispositivos/index.html")
