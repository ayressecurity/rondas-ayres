"""
Vistas de la app 'checkpoints' (modulo 4 — puntos de control).
Operan SIEMPRE dentro de la instalacion seleccionada (instalacion_id en sesion);
@requiere_instalacion redirige a Instalaciones/Clientes si falta contexto.

Seguridad: cada vista de detalle (editar/eliminar) verifica que el punto de
control pertenezca a la instalacion de la sesion (si no, 404). instalacion_id y
qr_token NUNCA vienen del formulario: los fija la vista.
"""
from io import BytesIO
from uuid import uuid4

import qrcode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.files.storage import default_storage
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from apps.comun.decoradores import requiere_instalacion
from .forms import PuntoControlForm
from .models import PuntoControl, TIPO_QR


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
            cp.tipo = TIPO_QR                                       # SIEMPRE lo fija el backend
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
            cp.tipo = TIPO_QR                                       # SIEMPRE lo fija el backend
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
def qr(request, pk):
    """
    Genera al vuelo el PNG del QR del punto de control. El contenido del QR es
    EXACTAMENTE el qr_token (solo el token; sin URLs ni ids internos), que es lo
    que escanea la app móvil. ?descargar=1 fuerza la descarga del archivo.
    """
    cp = _checkpoint_de_la_instalacion(request, pk)  # 404 si es de otra instalacion
    img = qrcode.make(cp.qr_token)
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    response = HttpResponse(buffer.getvalue(), content_type="image/png")
    if request.GET.get("descargar"):
        response["Content-Disposition"] = f'attachment; filename="qr-checkpoint-{cp.id}.png"'
    return response


@login_required
@requiere_instalacion
def mapa(request, pk):
    """
    Mini-página que embebe Google Maps centrado EXACTAMENTE en la lat/lng del
    punto de control. Se carga dentro de un iframe en el modal de la lista.
    El backend arma la URL leyendo lat/lng de la BD, así la posición mostrada
    coincide siempre con la del QR (la fuente es la misma fila). 404 si el punto
    es de otra instalacion.
    """
    cp = _checkpoint_de_la_instalacion(request, pk)
    # Coordenadas con punto decimal e independientes del locale (la plantilla
    # las interpola directo en la URL de Google Maps).
    src = f"https://maps.google.com/maps?q={cp.lat},{cp.lng}&z=17&hl=es&output=embed"
    return render(request, "checkpoints/mapa.html", {"cp": cp, "src": src})


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
