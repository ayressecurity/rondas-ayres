"""
BASE COMÚN de los informes del libro de novedades (solo lectura).

Los informes (Rondas, Novedades, ...) comparten estructura: listan
`libro_novedades` de la instalación seleccionada, ordenado de más reciente a más
antiguo, con filtro por tipo_evento (aplica_filtro) y filtro por fechas (GET).

Zona horaria: todo en America/Santiago (TIME_ZONE). Los rangos de fecha se
construyen aware en Santiago para filtrar timestamp_evento correctamente.
"""
from datetime import date, datetime, time, timedelta

from django.contrib.auth import get_user_model
from django.core.files.storage import default_storage
from django.core.paginator import Paginator
from django.db.models import CharField, Value
from django.db.models.functions import Cast, Lower, Replace
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone

POR_PAGINA = 20

from apps.novedades.models import LibroNovedades, LibroNovedadesMedia, TipoMedia

ANIO_BASE = 2026
MESES = [
    (1, "Enero"), (2, "Febrero"), (3, "Marzo"), (4, "Abril"),
    (5, "Mayo"), (6, "Junio"), (7, "Julio"), (8, "Agosto"),
    (9, "Septiembre"), (10, "Octubre"), (11, "Noviembre"), (12, "Diciembre"),
]


def _norm(kc):
    """Normaliza un keycloak_id para comparar: sin guiones, en minúsculas."""
    return (kc or "").replace("-", "").lower()


def _nombres_de_guardias(eventos):
    """Mapa keycloak_id_normalizado -> 'first_name last_name' de los guardias.

    cuentas_usuario.keycloak_id va SIN guiones y libro_novedades.guardia_keycloak_id
    CON guiones; por eso se normaliza (sin guiones, minúsculas) en AMBOS lados.
    Una sola consulta (sin N+1).
    """
    requeridos = {_norm(ev.guardia_keycloak_id) for ev in eventos if ev.guardia_keycloak_id}
    if not requeridos:
        return {}
    Usuario = get_user_model()
    # keycloak_id es UUIDField (se guarda sin guiones); lo pasamos a texto, le
    # quitamos guiones (no tiene) y lo bajamos a minúsculas para igualar al lado
    # del libro (CharField CON guiones, ya normalizado en `requeridos`).
    kc_texto = Cast("keycloak_id", output_field=CharField())
    filas = (
        Usuario.objects
        .annotate(kc_norm=Lower(Replace(kc_texto, Value("-"), Value(""), output_field=CharField())))
        .filter(kc_norm__in=requeridos)
        .values_list("kc_norm", "first_name", "last_name")
    )
    nombres = {}
    for kc_norm, first, last in filas:
        nombre = f"{first or ''} {last or ''}".strip()
        if nombre:
            nombres[kc_norm] = nombre
    return nombres


def anios_disponibles():
    """Años seleccionables: 2026 hasta el año actual (Santiago). Crece solo."""
    actual = timezone.localtime(timezone.now()).year
    return list(range(ANIO_BASE, max(actual, ANIO_BASE) + 1))


def _aware(d, t=time.min):
    """datetime aware en la zona activa (Santiago) desde fecha (+ hora)."""
    return timezone.make_aware(datetime.combine(d, t))


def _rango_y_label(request):
    """Lee los filtros de fecha del GET -> (rango, etiqueta, valores).

    rango = (inicio, fin) aware en Santiago, o None (sin filtro = todo).
    Precedencia: día > rango libre (desde/hasta) > mes(+año) > año.
    valores repinta el formulario.
    """
    g = request.GET
    dia = (g.get("dia") or "").strip()
    fini = (g.get("fini") or "").strip()   # fecha inicio del rango
    ffin = (g.get("ffin") or "").strip()   # fecha fin del rango
    mes = (g.get("mes") or "").strip()
    anio = (g.get("anio") or "").strip()
    valores = {"dia": dia, "fini": fini, "ffin": ffin, "mes": mes, "anio": anio}

    if dia:  # día exacto (YYYY-MM-DD)
        try:
            d = date.fromisoformat(dia)
            return (_aware(d), _aware(d + timedelta(days=1))), f"Día {d.isoformat()}", valores
        except ValueError:
            pass

    if fini and ffin:  # rango libre [desde, hasta] (ambos inclusive)
        try:
            d1 = date.fromisoformat(fini)
            d2 = date.fromisoformat(ffin)
            if d1 > d2:
                d1, d2 = d2, d1  # tolera fechas invertidas
            etiqueta = f"{d1.isoformat()} a {d2.isoformat()}"
            return (_aware(d1), _aware(d2 + timedelta(days=1))), etiqueta, valores
        except ValueError:
            pass

    if anio and mes:  # mes + año
        try:
            a, m = int(anio), int(mes)
            inicio = date(a, m, 1)
            fin = date(a + 1, 1, 1) if m == 12 else date(a, m + 1, 1)
            return (_aware(inicio), _aware(fin)), f"{dict(MESES)[m]} {a}", valores
        except (ValueError, KeyError):
            pass

    if anio:  # año completo
        try:
            a = int(anio)
            return (_aware(date(a, 1, 1)), _aware(date(a + 1, 1, 1))), f"Año {a}", valores
        except ValueError:
            pass

    return None, "Todos", valores


def eventos_filtrados(request, aplica_filtro):
    """Eventos de la instalación en sesión con filtro de tipo + filtro de fechas.

    Devuelve (eventos, rango, etiqueta, valores). Cada evento queda con
    .guardia_nombre resuelto (nombre del usuario o, si no hay, el UUID).
    """
    rango, etiqueta, valores = _rango_y_label(request)
    qs = (
        LibroNovedades.objects
        .filter(instalacion_id=request.session["instalacion_id"])
        .select_related("tipo_evento", "punto_control")
        .order_by("-timestamp_evento")
    )
    qs = aplica_filtro(qs)
    if rango:
        qs = qs.filter(timestamp_evento__gte=rango[0], timestamp_evento__lt=rango[1])
    eventos = list(qs)

    nombres = _nombres_de_guardias(eventos)
    for ev in eventos:
        ev.guardia_nombre = nombres.get(_norm(ev.guardia_keycloak_id)) or ev.guardia_keycloak_id or "—"
    return eventos, rango, etiqueta, valores


def _adjuntar_fotos(page_obj):
    """Setea ev.foto_url (URL en MEDIA de la 1ª foto del evento) o None.

    Solo consulta los medios de los eventos de la página actual (no todo).
    """
    ids = [ev.id for ev in page_obj]
    fotos = {}
    if ids:
        medios = (
            LibroNovedadesMedia.objects
            .filter(libro_novedades_id__in=ids, tipo=TipoMedia.FOTO)
            .order_by("id")
            .values_list("libro_novedades_id", "path")
        )
        for libro_id, path in medios:
            fotos.setdefault(libro_id, path)  # primera foto por evento
    for ev in page_obj:
        path = fotos.get(ev.id)
        ev.foto_url = default_storage.url(path) if path else None


def render_informe(request, *, titulo, aplica_filtro, export_url=None,
                   template="informes/informe.html", con_imagen=False):
    """Renderiza el informe (tabla + filtros + paginador). export_url = nombre de
    ruta de exportación a Excel (opcional). con_imagen adjunta la foto por fila
    (Informe de Novedades). template permite variar las columnas por informe."""
    eventos, _rango, etiqueta, valores = eventos_filtrados(request, aplica_filtro)

    # Paginación de a 20, respetando el filtro (se pagina el resultado filtrado).
    page_obj = Paginator(eventos, POR_PAGINA).get_page(request.GET.get("page"))

    if con_imagen:
        _adjuntar_fotos(page_obj)

    # Querystring del filtro SIN 'page' (para los enlaces del paginador y export).
    params = request.GET.copy()
    params.pop("page", None)
    query_sin_page = params.urlencode()

    contexto = {
        "titulo": titulo,
        "page_obj": page_obj,
        "anios": anios_disponibles(),
        "meses": MESES,
        "filtro": valores,
        "filtro_label": etiqueta,
        "export_url": reverse(export_url) if export_url else None,
        "query_sin_page": query_sin_page,
    }
    return render(request, template, contexto)
