"""
Módulo Dispositivos (web, SSPP/super_admin). Operan SIEMPRE dentro de la
instalación seleccionada (instalacion_id en sesión; @requiere_instalacion).

Aquí el SSPP:
  - genera / ve / rota el QR FIJO de enrolamiento de la instalación, e
  - lista / revoca / reactiva los dispositivos ya enrolados.

El secreto del QR es PROPIO de Rondas (instalacion.qr) y se guarda con
save(update_fields=["qr"]) — NUNCA vía sync. Acceso restringido a super_admin y
SSPP (ver _puede_administrar).
"""
import base64
import json
from functools import wraps
from io import BytesIO

import qrcode
from django.contrib import messages
from django.core.paginator import Paginator
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from apps.comun.decoradores import requiere_instalacion
from apps.cuentas import permisos
from apps.espejo.models import Instalacion
from .models import Dispositivo
from .utils import generar_secreto

POR_PAGINA = 20

# SSPP = rol de realm "sspp" (Seguridad Pública). Los roles del realm aún están
# "dormidos" (hoy el gateo real es super_admin), pero dejamos el módulo listo:
# super_admin SIEMPRE entra; quien tenga el rol "sspp" también.
ROL_SSPP = "sspp"


def _puede_administrar(request):
    """True para super_admin o para el rol SSPP del token."""
    return permisos.es_super_admin(request) or ROL_SSPP in permisos.roles_de(request)


def solo_sspp(view):
    """Restringe la vista a SSPP / super_admin. Si no, redirige al dashboard."""
    @wraps(view)
    def _wrapped(request, *args, **kwargs):
        if not _puede_administrar(request):
            messages.error(request, "Acceso restringido a SSPP y super administradores.")
            return redirect("comun:dashboard")
        return view(request, *args, **kwargs)
    return _wrapped


def _instalacion_actual(request):
    """Instalación de la sesión (espejo), o 404 si no existe la fila."""
    inst = Instalacion.objects.filter(id=request.session["instalacion_id"]).first()
    if inst is None:
        raise Http404("Instalación no encontrada.")
    return inst


def _secreto_unico():
    """Secreto de enrolamiento que no choque con el de otra instalación (qr es unique)."""
    for _ in range(5):
        secreto = generar_secreto()
        if not Instalacion.objects.filter(qr=secreto).exists():
            return secreto
    raise RuntimeError("No se pudo generar un secreto de enrolamiento único.")


def _payload_qr(request, instalacion):
    """Contenido del QR: JSON mínimo {server, s}. 's' = secreto que escanea el móvil."""
    return json.dumps(
        {"server": request.get_host(), "s": instalacion.qr},
        separators=(",", ":"),
    )


def _png_qr(contenido, box_size=None):
    """PNG (bytes) del QR para un contenido dado."""
    img = qrcode.make(contenido, box_size=box_size) if box_size else qrcode.make(contenido)
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()


@login_required
@requiere_instalacion
@solo_sspp
def index(request):
    """Panel del módulo: QR de enrolamiento + lista de dispositivos de la instalación."""
    inst = _instalacion_actual(request)
    dispositivos = (
        Dispositivo.objects
        .filter(instalacion_id=inst.id)
        .order_by("-creado_en", "-id")
    )
    page = Paginator(dispositivos, POR_PAGINA).get_page(request.GET.get("page"))
    return render(request, "dispositivos/index.html", {
        "instalacion": inst,
        "tiene_qr": bool(inst.qr),
        "page_obj": page,
        "query_sin_page": "",  # el paginador compartido lo espera
    })


@login_required
@requiere_instalacion
@solo_sspp
@require_POST
def generar(request):
    """Genera el secreto de enrolamiento si la instalación aún NO tiene.

    No sobreescribe uno existente: para cambiarlo se usa Rotar (acción explícita)."""
    inst = _instalacion_actual(request)
    if not inst.qr:
        inst.qr = _secreto_unico()
        inst.save(update_fields=["qr"])  # campo propio de Rondas; NUNCA vía sync
        messages.success(request, "QR de enrolamiento generado. Imprímelo y pégalo en el puesto.")
    return redirect("dispositivos:index")


@login_required
@requiere_instalacion
@solo_sspp
@require_POST
def rotar(request):
    """Regenera el secreto. Invalida el cartel anterior para NUEVOS enrolamientos.

    Rotar NO revoca ni desactiva los dispositivos ya enrolados: cada uno conserva
    su token individual y sigue activo."""
    inst = _instalacion_actual(request)
    inst.qr = _secreto_unico()
    inst.save(update_fields=["qr"])
    messages.success(
        request,
        "QR rotado. El cartel anterior ya no sirve para nuevos enrolamientos; "
        "los dispositivos ya enrolados siguen activos.",
    )
    return redirect("dispositivos:index")


@login_required
@requiere_instalacion
@solo_sspp
def qr_imagen(request):
    """PNG del QR generado al vuelo. ?descargar=1 fuerza la descarga del archivo."""
    inst = _instalacion_actual(request)
    if not inst.qr:
        raise Http404("La instalación no tiene QR de enrolamiento.")
    response = HttpResponse(_png_qr(_payload_qr(request, inst)), content_type="image/png")
    if request.GET.get("descargar"):
        response["Content-Disposition"] = f'attachment; filename="qr-enrolamiento-{inst.codigo}.png"'
    return response


@login_required
@requiere_instalacion
@solo_sspp
def imprimir(request):
    """Hoja imprimible (standalone) con el QR de enrolamiento de la instalación."""
    inst = _instalacion_actual(request)
    if not inst.qr:
        messages.error(request, "Genera el QR antes de imprimirlo.")
        return redirect("dispositivos:index")
    png = _png_qr(_payload_qr(request, inst), box_size=10)
    data_uri = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
    return render(request, "dispositivos/imprimir.html", {"instalacion": inst, "qr": data_uri})


def _dispositivo_de_instalacion(request, pk):
    """Dispositivo del pk que pertenezca a la instalación en sesión, o 404."""
    disp = Dispositivo.objects.filter(pk=pk, instalacion_id=request.session["instalacion_id"]).first()
    if disp is None:
        raise Http404("Dispositivo no encontrado.")
    return disp


@login_required
@requiere_instalacion
@solo_sspp
@require_POST
def eliminar(request, pk):
    """Elimina un dispositivo (borrado REAL de la fila), con aislamiento por
    instalación (_dispositivo_de_instalacion -> 404 si es de otra).

    Su token_hash se libera y el teléfono deja de autenticar. Las marcas
    históricas (libro_novedades.dispositivo_id) conservan el id: al no haber FK,
    quedan como referencia huérfana (no cascadea ni falla). Solo web."""
    disp = _dispositivo_de_instalacion(request, pk)
    disp.delete()
    messages.success(request, "Dispositivo eliminado.")
    return redirect("dispositivos:index")
