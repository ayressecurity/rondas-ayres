"""
Vistas de la app 'checkpoints' (modulo 4 — puntos de control).
Operan SIEMPRE dentro de la instalacion seleccionada (instalacion_id en sesion);
@requiere_instalacion redirige a Instalaciones/Clientes si falta contexto.

Seguridad: cada vista de detalle (editar/eliminar) verifica que el punto de
control pertenezca a la instalacion de la sesion (si no, 404). instalacion_id y
qr_token NUNCA vienen del formulario: los fija la vista.
"""
import base64
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
from .services import ConfigQrInvalida, aplicar_configuracion_qr


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
    # Grilla FIJA de 4 columnas x 7 filas = 28 por hoja. Cada hoja muestra los 28
    # slots (los sobrantes quedan vacíos pero visibles). Paginar de a 28.
    por_hoja = 28
    paginas = []
    for i in range(0, len(items), por_hoja):
        chunk = items[i:i + por_hoja]
        chunk += [None] * (por_hoja - len(chunk))  # rellena hasta 28 con vacíos
        paginas.append(chunk)
    return render(request, "checkpoints/imprimir.html", {"paginas": paginas})


def _punto_por_qr(qr_token, instalacion_id):
    """PuntoControl activo del qr_token que pertenezca a esa instalación."""
    if not qr_token or not instalacion_id:
        return None
    return PuntoControl.objects.filter(
        qr_token=qr_token,
        activo=True,
        instalacion_id=instalacion_id,
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
    instalacion_id = request.session.get("instalacion_id")
    if not instalacion_id:
        return JsonResponse({"ok": False, "error": "Selecciona una instalación primero."}, status=400)
    cp = _punto_por_qr((request.POST.get("qr_token") or "").strip(), instalacion_id)
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

    instalacion_id = request.session.get("instalacion_id")
    if not instalacion_id:
        return JsonResponse({"ok": False, "error": "Selecciona una instalación primero."}, status=400)

    cp = _punto_por_qr((request.POST.get("qr_token") or "").strip(), instalacion_id)
    if cp is None:
        return JsonResponse(
            {"ok": False, "error": "Este QR no pertenece a esta instalación."}, status=404
        )

    # Validación + UPDATE en el service compartido (misma lógica que la API móvil).
    # Mismos mensajes y mismos 400 que respondía esta vista inline.
    try:
        aplicar_configuracion_qr(
            cp,
            lat=request.POST.get("lat"),
            lng=request.POST.get("lng"),
            tolerancia_mts=request.POST.get("tolerancia_mts"),
            no_validar=request.POST.get("no_validar"),
        )
    except ConfigQrInvalida as exc:
        return JsonResponse({"ok": False, "error": exc.mensaje}, status=400)

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
