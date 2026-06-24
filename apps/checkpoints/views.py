"""
Vistas de la app 'checkpoints' (modulo 4 — puntos de control).
Operan SIEMPRE dentro de la instalacion seleccionada (instalacion_id en sesion);
@requiere_instalacion redirige a Instalaciones/Clientes si falta contexto.

Seguridad: cada vista de detalle (editar/eliminar) verifica que el punto de
control pertenezca a la instalacion de la sesion (si no, 404). instalacion_id y
qr_token NUNCA vienen del formulario: los fija la vista.
"""
import base64
from decimal import Decimal, InvalidOperation
from io import BytesIO
from uuid import uuid4

import qrcode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.files.storage import default_storage
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.comun.decoradores import requiere_instalacion, solo_super_admin
from apps.cuentas import permisos
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


def _qr_data_uri(token):
    """PNG del QR (contenido = qr_token) como data URI base64, para incrustarlo
    en la hoja imprimible sin requests extra y con nitidez."""
    img = qrcode.make(token, box_size=10, border=2)
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    b64 = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{b64}"


@login_required
@requiere_instalacion
def imprimir(request):
    """Hoja imprimible (grilla 3x3 = 9 por página) con los QR de los puntos de
    control ACTIVOS de la instalación en sesión, cada uno con su nombre debajo."""
    checkpoints = (
        PuntoControl.objects
        .filter(instalacion_id=request.session["instalacion_id"], activo=True)
        .order_by("nombre")
    )
    items = [{"nombre": cp.nombre, "qr": _qr_data_uri(cp.qr_token)} for cp in checkpoints]
    # Paginar de a 9 (3 columnas x 3 filas por hoja).
    paginas = [items[i:i + 9] for i in range(0, len(items), 9)]
    return render(request, "checkpoints/imprimir.html", {"paginas": paginas})


def _coord(valor):
    """Convierte el string del POST a Decimal SIN truncar; None si no es válido."""
    if valor is None or str(valor).strip() == "":
        return None
    try:
        return Decimal(str(valor).strip())
    except (InvalidOperation, ValueError):
        return None


def _punto_por_qr(request, qr_token):
    """PuntoControl activo del qr_token que pertenezca a la instalación en sesión."""
    if not qr_token:
        return None
    return PuntoControl.objects.filter(
        qr_token=qr_token,
        activo=True,
        instalacion_id=request.session["instalacion_id"],
    ).first()


@login_required
@requiere_instalacion
@solo_super_admin
def configurar_qr(request):
    """Pantalla de configuración por escaneo (cámara + GPS). Solo super_admin.
    Respaldo web de lo que hará la app móvil: graba ubicación real al QR."""
    return render(request, "checkpoints/configurar_qr.html")


@login_required
@require_POST
def configurar_qr_buscar(request):
    """Valida el QR escaneado (de esta instalación) y devuelve su config actual."""
    if not permisos.es_super_admin(request):
        return JsonResponse({"ok": False, "error": "No autorizado."}, status=403)
    cp = _punto_por_qr(request, (request.POST.get("qr_token") or "").strip())
    if cp is None:
        return JsonResponse(
            {"ok": False, "error": "Este QR no pertenece a esta instalación."}, status=404
        )
    return JsonResponse({
        "ok": True,
        "nombre": cp.nombre,
        "tolerancia_mts": cp.tolerancia_mts,
        "validar_posicion": cp.validar_posicion,
    })


@login_required
@require_POST
def configurar_qr_guardar(request):
    """UPDATE del punto_control: lat/lng del teléfono + tolerancia + validar.
    NO cambia qr_token, nombre ni tipo. Solo super_admin y de esta instalación."""
    if not permisos.es_super_admin(request):
        return JsonResponse({"ok": False, "error": "No autorizado."}, status=403)

    cp = _punto_por_qr(request, (request.POST.get("qr_token") or "").strip())
    if cp is None:
        return JsonResponse(
            {"ok": False, "error": "Este QR no pertenece a esta instalación."}, status=404
        )

    lat = _coord(request.POST.get("lat"))
    lng = _coord(request.POST.get("lng"))
    if lat is None or lng is None:
        return JsonResponse(
            {"ok": False, "error": "Falta la ubicación: debes permitir el GPS para configurar."},
            status=400,
        )

    # Tolerancia entera >= 0; si no viene válida, conserva la actual.
    try:
        tolerancia = int(request.POST.get("tolerancia_mts"))
        if tolerancia < 0:
            raise ValueError
    except (TypeError, ValueError):
        tolerancia = cp.tolerancia_mts

    no_validar = request.POST.get("no_validar") in ("1", "true", "on", "True")

    cp.lat = lat                      # coordenadas reales del teléfono, sin truncar
    cp.lng = lng
    cp.validar_posicion = not no_validar
    cp.tolerancia_mts = tolerancia
    cp.save(update_fields=["lat", "lng", "validar_posicion", "tolerancia_mts"])

    return JsonResponse({"ok": True, "nombre": cp.nombre})


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
