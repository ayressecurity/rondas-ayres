"""
Módulo "Eventos en tiempo real" (solo SSPP / super_admin).

Página GLOBAL: NO exige cliente/instalación en sesión (como Inicio) -> usa
@login_required + @solo_sspp, sin @requiere_instalacion. Muestra TODOS los eventos
de TODAS las instalaciones (libro_novedades), orden por id DESC, paginados de 65.

Reutiliza la lógica YA existente de los informes, sin duplicar:
  - nombre del guardia -> apps.informes.base._nombres_de_guardias (+ norm_keycloak_id).
  - fotos del evento    -> apps.informes.base._adjuntar_fotos (setea ev.foto_urls).

El cliente y la instalación se resuelven desde el espejo por MAPAS batch (sin FK,
sin N+1), igual patrón que _nombres_de_guardias.
"""
import json
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone

from apps.cuentas import permisos
from apps.cuentas.identidad import norm_keycloak_id as _norm
from apps.espejo.models import Cliente, Instalacion
from apps.informes.base import _adjuntar_fotos, _nombres_de_guardias
from apps.novedades.models import LibroNovedades

POR_PAGINA = 65

# tipo_evento.codigo -> clase CSS de fondo suave (pastel). Los códigos no listados
# (p.ej. codigo_no_existe) quedan neutros (sin clase).
COLOR_POR_CODIGO = {
    "novedad": "fila-novedad",              # amarillo suave
    "arribo": "fila-arribo",                # verde suave
    "arribo_sin_geo": "fila-arribo",
    "arribo_invalido": "fila-arribo",
    "sesion_inicio": "fila-sesion-inicio",  # celeste suave
    "sesion_fin": "fila-sesion-fin",        # azul claro suave
    "ronda_cancelada": "fila-cancelada",    # rojo claro suave
}


def solo_sspp(view):
    """Restringe a SSPP / super_admin (mismo criterio que el módulo Dispositivos)."""
    @wraps(view)
    def _wrapped(request, *args, **kwargs):
        if not (permisos.es_super_admin(request) or "sspp" in permisos.roles_de(request)):
            messages.error(request, "Acceso restringido a SSPP y super administradores.")
            return redirect("comun:dashboard")
        return view(request, *args, **kwargs)
    return _wrapped


def _pagina(request):
    """Página de 65 eventos (id DESC). Congela la página a lista estable (65 filas)
    para poder resolver guardia/fotos sobre los MISMOS objetos (Paginator sobre un
    queryset solo trae esas 65 filas vía LIMIT/OFFSET; no carga toda la tabla)."""
    qs = (
        LibroNovedades.objects
        .select_related("tipo_evento", "punto_control")
        .order_by("-id")
    )
    page_obj = Paginator(qs, POR_PAGINA).get_page(request.GET.get("page"))
    page_obj.object_list = list(page_obj.object_list)  # congela a lista estable
    return page_obj


def _mapas_espejo(eventos):
    """(instalaciones, clientes) para resolver nombres desde el espejo, en 2 queries.

    instalaciones: {id -> {"nombre", "cliente_id"}}. clientes: {id -> razon_social}."""
    inst_ids = {ev.instalacion_id for ev in eventos if ev.instalacion_id}
    instalaciones = {
        i["id"]: i
        for i in Instalacion.objects.filter(id__in=inst_ids).values("id", "nombre", "cliente_id")
    }
    cliente_ids = {i["cliente_id"] for i in instalaciones.values()}
    clientes = dict(Cliente.objects.filter(id__in=cliente_ids).values_list("id", "razon_social"))
    return instalaciones, clientes


def _filas(page_obj):
    """Lista de dicts con TODO ya resuelto (para el template y para el JSON)."""
    eventos = page_obj.object_list

    # Nombre del guardia: MISMA lógica que los informes (sin duplicar).
    nombres = _nombres_de_guardias(eventos)
    for ev in eventos:
        ev.guardia_nombre = nombres.get(_norm(ev.guardia_keycloak_id)) or ev.guardia_keycloak_id or "—"
    # Fotos del evento (setea ev.foto_urls): reutiliza el helper de los informes.
    _adjuntar_fotos(page_obj)

    instalaciones, clientes = _mapas_espejo(eventos)

    filas = []
    for ev in eventos:
        inst = instalaciones.get(ev.instalacion_id)
        instalacion_nombre = inst["nombre"] if inst else "—"
        cliente_nombre = clientes.get(inst["cliente_id"], "—") if inst else "—"
        codigo = ev.tipo_evento.codigo
        fotos = ev.foto_urls or []
        # Botón de fotos si el evento tiene imágenes, o si es sesion_inicio (que
        # abre el modal con "sin imagen" cuando el guardia no subió foto).
        tiene_boton = bool(fotos) or codigo == "sesion_inicio"
        filas.append({
            "id": ev.id,
            "fecha": timezone.localtime(ev.timestamp_evento).strftime("%d-%m-%Y %H:%M:%S"),
            "tipo": ev.tipo_evento.nombre,
            "codigo": codigo,
            "cliente": cliente_nombre,
            "instalacion": instalacion_nombre,
            "punto": ev.punto_control.nombre if ev.punto_control else "—",
            "guardia": ev.guardia_nombre,
            "texto": ev.texto or "—",
            "color": COLOR_POR_CODIGO.get(codigo, ""),
            "tiene_boton": tiene_boton,
            "fotos": fotos,
            "fotos_json": json.dumps(fotos),  # para el atributo data-* del template
        })
    return filas


@login_required
@solo_sspp
def index(request):
    """Página con la tabla (render inicial server-side) + modal + auto-refresco."""
    page_obj = _pagina(request)
    return render(request, "tiempo_real/index.html", {
        "filas": _filas(page_obj),
        "page_obj": page_obj,
        "query_sin_page": "",  # el paginador compartido lo espera
    })


@login_required
@solo_sspp
def data(request):
    """JSON de la página (?page=N) para el auto-refresco AJAX cada 2s."""
    page_obj = _pagina(request)
    return JsonResponse({
        "eventos": _filas(page_obj),
        "page": page_obj.number,
        "num_pages": page_obj.paginator.num_pages,
        "has_previous": page_obj.has_previous(),
        "has_next": page_obj.has_next(),
    })
