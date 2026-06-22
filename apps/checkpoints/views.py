"""
Vistas de la app 'checkpoints' (modulo 4 — puntos de control).
Operan SIEMPRE dentro de la instalacion seleccionada (instalacion_id en sesion);
@requiere_instalacion redirige a Instalaciones/Clientes si falta contexto.
"""
from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from apps.comun.decoradores import requiere_instalacion
from .models import PuntoControl


@login_required
@requiere_instalacion
def index(request):
    """Lista (solo lectura) de los puntos de control activos de la instalacion."""
    checkpoints = (
        PuntoControl.objects
        .filter(instalacion_id=request.session["instalacion_id"], activo=True)
        .order_by("nombre")
    )
    return render(request, "checkpoints/index.html", {"checkpoints": checkpoints})
