"""
Vistas de la app 'checkpoints' (modulo 4 — puntos de control).
Operan SIEMPRE dentro de la instalacion seleccionada (instalacion_id en sesion);
@requiere_instalacion redirige a Instalaciones/Clientes si falta contexto.

Seguridad: cada vista de detalle (editar/eliminar) verifica que el punto de
control pertenezca a la instalacion de la sesion (si no, 404). instalacion_id y
qr_token NUNCA vienen del formulario: los fija la vista.
"""
from uuid import uuid4

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.files.storage import default_storage
from django.shortcuts import get_object_or_404, redirect, render

from apps.comun.decoradores import requiere_instalacion
from .forms import PuntoControlForm
from .models import PuntoControl


def _checkpoint_de_la_instalacion(request, pk):
    """PuntoControl activo del pk que pertenezca a la instalacion en sesion, o 404."""
    return get_object_or_404(
        PuntoControl,
        pk=pk,
        instalacion_id=request.session["instalacion_id"],
        activo=True,
    )


def _guardar_foto(archivo):
    """Guarda la foto subida en MEDIA y devuelve su path relativo (o None)."""
    if not archivo:
        return None
    return default_storage.save(f"checkpoints/{uuid4().hex}_{archivo.name}", archivo)


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


@login_required
@requiere_instalacion
def nuevo(request):
    """Alta de un punto de control en la instalacion seleccionada."""
    if request.method == "POST":
        form = PuntoControlForm(request.POST, request.FILES)
        if form.is_valid():
            cp = form.save(commit=False)
            cp.instalacion_id = request.session["instalacion_id"]   # SIEMPRE de la sesion
            cp.validar_posicion = not form.cleaned_data["no_validar"]
            cp.qr_token = str(uuid4())                              # NOT NULL unique
            cp.activo = True
            foto_path = _guardar_foto(form.cleaned_data.get("foto"))
            if foto_path:
                cp.foto_path = foto_path
            cp.save()
            messages.success(request, f"Punto de control «{cp.nombre}» creado.")
            return redirect("checkpoints:index")
    else:
        form = PuntoControlForm()
    return render(request, "checkpoints/form.html", {"form": form, "modo": "nuevo"})


@login_required
@requiere_instalacion
def editar(request, pk):
    """Edita un punto de control existente. NO regenera qr_token."""
    cp = _checkpoint_de_la_instalacion(request, pk)
    if request.method == "POST":
        form = PuntoControlForm(request.POST, request.FILES, instance=cp)
        if form.is_valid():
            cp = form.save(commit=False)
            cp.validar_posicion = not form.cleaned_data["no_validar"]
            foto_path = _guardar_foto(form.cleaned_data.get("foto"))
            if foto_path:
                cp.foto_path = foto_path
            cp.save()
            messages.success(request, f"Punto de control «{cp.nombre}» actualizado.")
            return redirect("checkpoints:index")
    else:
        form = PuntoControlForm(instance=cp, initial={"no_validar": not cp.validar_posicion})
    return render(request, "checkpoints/form.html", {"form": form, "modo": "editar", "cp": cp})


@login_required
@requiere_instalacion
def eliminar(request, pk):
    """Soft delete (activo=False), con confirmacion previa."""
    cp = _checkpoint_de_la_instalacion(request, pk)
    if request.method == "POST":
        cp.activo = False
        cp.save(update_fields=["activo"])
        messages.success(request, f"Punto de control «{cp.nombre}» eliminado.")
        return redirect("checkpoints:index")
    return render(request, "checkpoints/eliminar.html", {"cp": cp})
