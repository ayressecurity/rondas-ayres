"""
Vistas de la app 'rondas' (módulos 5-6). Operan SIEMPRE dentro de la instalación
seleccionada (instalacion_id en sesión); @requiere_instalacion redirige si falta.

Secciones implementadas: 1) crear/editar ronda  2) asignar checkpoints
(ronda_secuencia). La sección 3 (programación de alertas) va aparte.

Seguridad: cada detalle (editar/eliminar) verifica que la ronda pertenezca a la
instalación de la sesión (si no, 404). cliente_id / instalacion_id NUNCA vienen
del formulario: los fija la vista desde la sesión.
"""
import random

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render

from apps.comun.decoradores import requiere_instalacion
from .forms import RondaForm
from .models import Ronda, RondaSecuencia, EstadoGenerico


def _ronda_de_la_instalacion(request, pk):
    """Ronda activa del pk que pertenezca a la instalación en sesión, o 404."""
    return get_object_or_404(
        Ronda,
        pk=pk,
        instalacion_id=request.session["instalacion_id"],
        estado=EstadoGenerico.ACTIVA,
    )


def _guardar_secuencia(ronda, puntos, orden_aleatorio):
    """Reescribe la secuencia de la ronda con los puntos elegidos.

    - aleatorio: barajamos y asignamos orden 1..n.
    - a gusto del guardia: orden 0 para todos (no se exige orden en terreno).
    """
    ronda.rondasecuencia_set.all().delete()
    puntos = list(puntos)
    if orden_aleatorio:
        random.shuffle(puntos)
        filas = [
            RondaSecuencia(ronda=ronda, punto_control=p, orden=i)
            for i, p in enumerate(puntos, start=1)
        ]
    else:
        filas = [RondaSecuencia(ronda=ronda, punto_control=p, orden=0) for p in puntos]
    RondaSecuencia.objects.bulk_create(filas)


@login_required
@requiere_instalacion
def index(request):
    """Lista (solo las activas) de las rondas de la instalación, con N° de checkpoints."""
    rondas = (
        Ronda.objects
        .filter(instalacion_id=request.session["instalacion_id"], estado=EstadoGenerico.ACTIVA)
        .annotate(num_checkpoints=Count("rondasecuencia"))
        .order_by("-creado_en")
    )
    return render(request, "rondas/index.html", {"rondas": rondas})


@login_required
@requiere_instalacion
def nueva(request):
    """Alta de una ronda + su secuencia de checkpoints."""
    instalacion_id = request.session["instalacion_id"]
    if request.method == "POST":
        form = RondaForm(request.POST, instalacion_id=instalacion_id)
        if form.is_valid():
            with transaction.atomic():
                ronda = form.save(commit=False)
                ronda.cliente_id = request.session["cliente_id"]      # SIEMPRE de la sesión
                ronda.instalacion_id = instalacion_id                 # SIEMPRE de la sesión
                ronda.orden_aleatorio = form.orden_aleatorio
                ronda.estado = EstadoGenerico.ACTIVA
                ronda.save()
                _guardar_secuencia(ronda, form.cleaned_data["puntos"], form.orden_aleatorio)
            messages.success(request, f"Ronda «{ronda.nombre}» creada.")
            return redirect("rondas:index")
    else:
        form = RondaForm(instalacion_id=instalacion_id)
    return render(request, "rondas/form.html", {"form": form, "modo": "nueva"})


@login_required
@requiere_instalacion
def editar(request, pk):
    """Edita una ronda y reescribe su secuencia de checkpoints."""
    ronda = _ronda_de_la_instalacion(request, pk)
    instalacion_id = request.session["instalacion_id"]
    if request.method == "POST":
        form = RondaForm(request.POST, instance=ronda, instalacion_id=instalacion_id)
        if form.is_valid():
            with transaction.atomic():
                ronda = form.save(commit=False)
                ronda.orden_aleatorio = form.orden_aleatorio
                ronda.save()
                _guardar_secuencia(ronda, form.cleaned_data["puntos"], form.orden_aleatorio)
            messages.success(request, f"Ronda «{ronda.nombre}» actualizada.")
            return redirect("rondas:index")
    else:
        form = RondaForm(instance=ronda, instalacion_id=instalacion_id)
    return render(request, "rondas/form.html", {"form": form, "modo": "editar", "ronda": ronda})


@login_required
@requiere_instalacion
def eliminar(request, pk):
    """Baja lógica: estado = inactiva (no borrado físico), con confirmación."""
    ronda = _ronda_de_la_instalacion(request, pk)
    if request.method == "POST":
        ronda.estado = EstadoGenerico.INACTIVA
        ronda.save(update_fields=["estado"])
        messages.success(request, f"Ronda «{ronda.nombre}» eliminada.")
        return redirect("rondas:index")
    return render(request, "rondas/eliminar.html", {"ronda": ronda})
