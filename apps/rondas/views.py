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
from django.db.models import Count, Exists, OuterRef
from django.shortcuts import get_object_or_404, redirect, render

from apps.comun.decoradores import requiere_instalacion
from .forms import RondaForm
from .models import (
    Ronda,
    RondaSecuencia,
    Programacion,
    ProgramacionHorario,
    EstadoGenerico,
)


def _ronda_de_la_instalacion(request, pk):
    """Ronda activa del pk que pertenezca a la instalación en sesión, o 404."""
    return get_object_or_404(
        Ronda,
        pk=pk,
        instalacion_id=request.session["instalacion_id"],
        estado=EstadoGenerico.ACTIVA,
    )


def _guardar_secuencia(ronda, puntos_en_orden):
    """Reescribe la secuencia de la ronda asignando orden 1..n por posición."""
    ronda.rondasecuencia_set.all().delete()
    filas = [
        RondaSecuencia(ronda=ronda, punto_control=p, orden=i)
        for i, p in enumerate(puntos_en_orden, start=1)
    ]
    RondaSecuencia.objects.bulk_create(filas)


def _puntos_en_orden(form):
    """Lista ordenada de checkpoints para la secuencia, según el modo elegido.

    - aleatoria: TODOS los checkpoints activos de la instalación, barajados.
    - estipulada: los seleccionados, en el orden marcado por el usuario.
    """
    if form.orden_aleatorio:
        puntos = list(form.fields["puntos"].queryset)
        random.shuffle(puntos)
        return puntos
    return form.puntos_ordenados()


def _guardar_programacion(ronda, repite, horarios):
    """Reescribe la programación de la ronda.

    Borra la previa (horarios -> programacion, por el PROTECT) y, si hay `repite`,
    crea una Programacion + una ProgramacionHorario por cada horario. Sin `repite`
    la ronda queda sin programación.
    """
    progs = Programacion.objects.filter(ronda=ronda)
    ProgramacionHorario.objects.filter(programacion__in=progs).delete()
    progs.delete()
    if not repite:
        return
    prog = Programacion.objects.create(ronda=ronda, repite=repite, activo=True)
    ProgramacionHorario.objects.bulk_create(
        [ProgramacionHorario(programacion=prog, hora=h, minuto=m) for h, m in horarios]
    )


def _horarios_de_post(request):
    """Pares {hora, minuto} tal como llegaron (para re-pintar si hay error)."""
    horas = request.POST.getlist("hora")
    minutos = request.POST.getlist("minuto")
    filas = []
    for h, m in zip(horas, minutos):
        if (h or "").strip() == "" and (m or "").strip() == "":
            continue
        filas.append({"hora": h, "minuto": m})
    return filas


@login_required
@requiere_instalacion
def index(request):
    """Lista (solo las activas) de las rondas de la instalación, con N° de checkpoints."""
    rondas = (
        Ronda.objects
        .filter(instalacion_id=request.session["instalacion_id"], estado=EstadoGenerico.ACTIVA)
        .annotate(
            num_checkpoints=Count("rondasecuencia", distinct=True),
            tiene_prog=Exists(Programacion.objects.filter(ronda=OuterRef("pk"), activo=True)),
        )
        .order_by("-creado_en")
    )
    return render(request, "rondas/index.html", {"rondas": rondas})


@login_required
@requiere_instalacion
def nueva(request):
    """Alta de una ronda + su secuencia de checkpoints + programación (opcional)."""
    instalacion_id = request.session["instalacion_id"]
    if request.method == "POST":
        form = RondaForm(request.POST, instalacion_id=instalacion_id)
        horarios = _horarios_de_post(request)
        if form.is_valid():
            with transaction.atomic():
                ronda = form.save(commit=False)
                ronda.cliente_id = request.session["cliente_id"]      # SIEMPRE de la sesión
                ronda.instalacion_id = instalacion_id                 # SIEMPRE de la sesión
                ronda.orden_aleatorio = form.orden_aleatorio
                ronda.estado = EstadoGenerico.ACTIVA
                ronda.save()
                _guardar_secuencia(ronda, _puntos_en_orden(form))
                _guardar_programacion(ronda, form.cleaned_data.get("repite"), form.horarios)
            messages.success(request, f"Ronda «{ronda.nombre}» creada.")
            return redirect("rondas:index")
    else:
        form = RondaForm(instalacion_id=instalacion_id)
        horarios = []
    return render(request, "rondas/form.html", {"form": form, "modo": "nueva", "horarios": horarios})


@login_required
@requiere_instalacion
def editar(request, pk):
    """Edita una ronda y reescribe su secuencia de checkpoints y su programación."""
    ronda = _ronda_de_la_instalacion(request, pk)
    instalacion_id = request.session["instalacion_id"]
    if request.method == "POST":
        form = RondaForm(request.POST, instance=ronda, instalacion_id=instalacion_id)
        horarios = _horarios_de_post(request)
        if form.is_valid():
            with transaction.atomic():
                ronda = form.save(commit=False)
                ronda.orden_aleatorio = form.orden_aleatorio
                ronda.save()
                _guardar_secuencia(ronda, _puntos_en_orden(form))
                _guardar_programacion(ronda, form.cleaned_data.get("repite"), form.horarios)
            messages.success(request, f"Ronda «{ronda.nombre}» actualizada.")
            return redirect("rondas:index")
    else:
        # Cargar la programación existente para precargar repite + horarios.
        prog = Programacion.objects.filter(ronda=ronda, activo=True).first()
        form = RondaForm(
            instance=ronda,
            instalacion_id=instalacion_id,
            initial={"repite": prog.repite if prog else ""},
        )
        horarios = []
        if prog:
            horarios = [
                {"hora": ph.hora, "minuto": ph.minuto}
                for ph in ProgramacionHorario.objects.filter(programacion=prog).order_by("hora", "minuto")
            ]
    return render(request, "rondas/form.html", {"form": form, "modo": "editar", "ronda": ronda, "horarios": horarios})


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
