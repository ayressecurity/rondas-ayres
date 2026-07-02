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
from django.views.decorators.http import require_POST

from apps.cuentas import permisos
from apps.cuentas.identidad import norm_keycloak_id as _norm
from apps.espejo import repositorio
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
    """Restringe VER la Central a SSPP / cenapoc / cliente / super_admin.

    Ver la tabla ≠ poder comentar: sspp/cenapoc/cliente ven la tabla; solo cenapoc
    y super_admin pueden comentar (puede_comentar). El rol 'cliente' entra PERO su
    queryset llega SIEMPRE acotado a su cliente (_acotar_por_cliente)."""
    @wraps(view)
    def _wrapped(request, *args, **kwargs):
        if not (permisos.es_super_admin(request)
                or {"sspp", "cenapoc", "cliente"} & set(permisos.roles_de(request))):
            messages.error(request, "Acceso restringido a SSPP, cenapoc y super administradores.")
            return redirect("comun:dashboard")
        return view(request, *args, **kwargs)
    return _wrapped


def _acotar_por_cliente(request, qs):
    """Aísla la Central por cliente. PRECEDENCIA (falla CERRADO):

    - super_admin: ve TODO (rol maestro; ÚNICA excepción a la precedencia).
    - presencia del rol 'cliente' (aunque venga MEZCLADO con sspp/cenapoc): FUERZA
      el aislamiento. Con cliente_id resoluble a un cliente vigente -> solo eventos
      de SUS instalaciones. SIN cliente resoluble (claim ausente/mal/cliente
      borrado) -> queryset VACÍO. Un 'cliente' NUNCA ve todo.
    - sin rol 'cliente': monitoreo global (sspp/cenapoc) ve TODO (sin cambios).

    Ojo con el ORDEN: el check de 'cliente' va ANTES del atajo sspp/cenapoc; si no,
    un cliente+cenapoc caería en el atajo y vería todo (fallar ABIERTO)."""
    if permisos.es_super_admin(request):
        return qs
    roles = set(permisos.roles_de(request))
    if "cliente" in roles:
        cid = permisos.cliente_de(request)
        cli = repositorio.obtener_cliente(cid) if cid else None
        if not cli:
            return qs.none()   # rol cliente sin cliente válido = ve NADA
        inst_ids = list(
            Instalacion.objects.filter(cliente_id=cli.id, deleted_at__isnull=True)
            .values_list("id", flat=True)
        )
        return qs.filter(instalacion_id__in=inst_ids)
    if {"sspp", "cenapoc"} & roles:
        return qs
    return qs


def puede_comentar(request):
    """True si el usuario puede AGREGAR/EDITAR el comentario de la Central.

    Solo cenapoc y super_admin (NO sspp). Se valida en el servidor (endpoint 403)
    y decide si se pinta el botón de la columna Acción; no basta ocultar el botón."""
    return permisos.es_super_admin(request) or "cenapoc" in permisos.roles_de(request)


def es_vista_cliente(request):
    """La Central se muestra ACOTADA para el rol 'cliente' (empresa externa): sin
    las columnas Cliente (son todos suyos), Comentario ni Acción (notas internas de
    la central, no le competen). super_admin ve la tabla completa aunque llevara el
    rol. NO altera el filtrado/seguridad del 3.3a: es solo presentación."""
    return permisos.es_cliente(request) and not permisos.es_super_admin(request)


def _pagina(request):
    """Página de 65 eventos por HORA REAL del evento (timestamp_evento DESC), igual
    que los informes: así un evento OFFLINE (capturado antes pero recibido después,
    con id mayor) cae en su posición cronológica y no arriba por su id de llegada.
    Desempate por -id para un orden estable ante horas idénticas.

    Congela la página a lista estable (65 filas) para resolver guardia/fotos sobre
    los MISMOS objetos (Paginator sobre un queryset solo trae esas 65 filas vía
    LIMIT/OFFSET; no carga toda la tabla)."""
    qs = (
        LibroNovedades.objects
        .select_related("tipo_evento", "punto_control")
        .order_by("-timestamp_evento", "-id")
    )
    # Aislamiento: el rol 'cliente' solo ve su cliente; el resto (super/sspp/cenapoc)
    # ve todo. Se acota ANTES de paginar (el filtro no cambia el orden).
    qs = _acotar_por_cliente(request, qs)
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
        comentario = ev.comentario_central or ""
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
            # Comentario de la Central (3.2): el campo ya viene en ev (sin queries extra).
            "comentario_central": comentario,
            "tiene_comentario": bool(comentario),
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
        # Flag para pintar (o no) el botón de la columna Acción; el permiso real se
        # revalida SIEMPRE en el servidor (endpoint comentar).
        "puede_comentar": puede_comentar(request),
        # Vista ACOTADA del rol cliente: oculta Cliente/Comentario/Acción (solo UI).
        "es_vista_cliente": es_vista_cliente(request),
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
        # El JS usa este flag para decidir si pinta el botón tras cada refresco.
        "puede_comentar": puede_comentar(request),
        # Mismo flag que el render inicial: mantiene la PARIDAD de columnas en el AJAX.
        "es_vista_cliente": es_vista_cliente(request),
    })


@login_required
@require_POST
def comentar(request):
    """POST tiempo_real:comentar — guarda/edita el comentario_central de un evento.

    Solo cenapoc / super_admin (puede_comentar); si no -> 403 JSON. Recibe `id`
    (libro_novedades.id) y `comentario` (texto). Texto vacío -> NULL (borra el
    comentario); con contenido -> se guarda tal cual (sobrescribible/editable).
    NO toca ningún flujo de inserción: solo UPDATE de esta columna."""
    if not puede_comentar(request):
        return JsonResponse(
            {"error": "No tienes permiso para comentar."}, status=403
        )

    try:
        libro_id = int(request.POST.get("id") or 0)
    except (TypeError, ValueError):
        libro_id = 0
    if libro_id <= 0:
        return JsonResponse({"error": "Evento inválido."}, status=400)

    # Vacío -> None (permite borrar el comentario); con contenido -> tal cual.
    texto = (request.POST.get("comentario") or "").strip() or None

    actualizados = LibroNovedades.objects.filter(id=libro_id).update(comentario_central=texto)
    if not actualizados:
        return JsonResponse({"error": "Evento no encontrado."}, status=404)

    return JsonResponse({
        "id": libro_id,
        "comentario_central": texto or "",
        "tiene_comentario": bool(texto),
    })
