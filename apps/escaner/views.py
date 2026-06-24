"""
App 'escaner' — SIMULADOR DE EJECUCIÓN DE RONDA (testeo de la futura app móvil).
SOLO super_admin, dentro de la instalación seleccionada.

Flujo: el guardia elige una ronda activa -> se crea una ronda_ejecucion en curso
-> escanea los QR de los puntos (cámara + GPS obligatorio) -> cada escaneo se
registra en libro_novedades (con ronda_id = ronda en curso) y se recalcula el
progreso X/Y. Al completar todos los puntos, la ejecución pasa a 'completada'.

Conserva el registro existente en libro_novedades (arribo / arribo_invalido /
arribo_sin_geo, distancia haversine, lat/lng, observación, timestamps, guardia).
"""
from datetime import datetime, timedelta
from decimal import Decimal
from math import asin, cos, radians, sin, sqrt

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.checkpoints.models import PuntoControl
from apps.comun.decoradores import requiere_instalacion, solo_super_admin
from apps.cuentas import permisos
from apps.novedades.models import LibroNovedades, TipoEvento
from apps.rondas.models import Ronda, RondaSecuencia, EstadoGenerico
from .models import RondaEjecucion

# Radio medio de la Tierra en metros (para haversine).
RADIO_TIERRA_M = 6_371_000


def _haversine_m(lat1, lng1, lat2, lng2):
    """Distancia en metros entre dos puntos (lat/lng en grados) por haversine."""
    lat1, lng1, lat2, lng2 = map(radians, (lat1, lng1, lat2, lng2))
    dlat = lat2 - lat1
    dlng = lng2 - lng1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlng / 2) ** 2
    return 2 * RADIO_TIERRA_M * asin(sqrt(a))


def _parse_coord(valor):
    """Convierte el string del POST a float; None si falta o no es válido."""
    if valor is None or str(valor).strip() == "":
        return None
    try:
        return float(valor)
    except (TypeError, ValueError):
        return None


def _en_rango(t, ini, fin):
    """¿La hora t está dentro de [ini, fin]? Soporta rango que cruza medianoche."""
    if ini <= fin:
        return ini <= t <= fin
    return t >= ini or t <= fin  # cruza medianoche (ej. 19:00 -> 07:00)


def _ronda_para_ahora(instalacion_id):
    """Ronda activa de la instalación cuyo rango horario contiene la hora actual
    de Santiago (hora del SERVIDOR, no del navegador). None si ninguna aplica."""
    ahora = timezone.localtime(timezone.now()).time()
    rondas = (
        Ronda.objects
        .filter(
            instalacion_id=instalacion_id,
            estado=EstadoGenerico.ACTIVA,
            hora_inicio__isnull=False,
            hora_fin__isnull=False,
        )
        .order_by("nombre")
    )
    for r in rondas:
        if _en_rango(ahora, r.hora_inicio, r.hora_fin):
            return r
    return None


def _aware(fecha, hora):
    """datetime aware en la zona activa (Santiago) a partir de fecha + hora."""
    return timezone.make_aware(datetime.combine(fecha, hora))


def _ventana_turno(ronda, ref):
    """Ventana [inicio, fin] (datetimes reales) del turno que contiene `ref`.

    Maneja el cruce de medianoche: si el rango cruza (inicio > fin) y `ref` es de
    madrugada (antes del fin), el inicio del turno fue AYER. None si la ronda no
    tiene rango horario. `ref` es un datetime aware.
    """
    if ronda.hora_inicio is None or ronda.hora_fin is None:
        return None
    ref = timezone.localtime(ref)
    hoy = ref.date()
    ini, fin = ronda.hora_inicio, ronda.hora_fin
    if ini <= fin:
        return _aware(hoy, ini), _aware(hoy, fin)
    if ref.time() >= ini:  # noche, aún del mismo día -> fin es mañana
        return _aware(hoy, ini), _aware(hoy + timedelta(days=1), fin)
    return _aware(hoy - timedelta(days=1), ini), _aware(hoy, fin)  # madrugada -> inicio ayer


def _ejecucion_en_curso(guardia_keycloak_id):
    """Última ronda_ejecucion en curso del guardia (o None)."""
    return (
        RondaEjecucion.objects
        .filter(guardia_keycloak_id=guardia_keycloak_id, estado=RondaEjecucion.Estado.EN_CURSO)
        .order_by("-iniciada_en")
        .first()
    )


def _estado_ejecucion(ejecucion, ventana):
    """Progreso de la ejecución dentro de la VENTANA del turno: puntos en orden +
    cuáles ya escaneó ESE guardia para ESA ronda dentro de [inicio, fin].

    total = puntos en ronda_secuencia. escaneados = puntos DISTINTOS registrados
    por el guardia en la ventana (se lee de libro_novedades, no de la sesión).
    """
    inicio, fin = ventana
    secuencia = (
        RondaSecuencia.objects
        .filter(ronda_id=ejecucion.ronda_id)
        .select_related("punto_control")
        .order_by("orden")
    )
    punto_ids = [s.punto_control_id for s in secuencia]
    completados = set(
        LibroNovedades.objects
        .filter(
            ronda_id=ejecucion.ronda_id,
            guardia_keycloak_id=ejecucion.guardia_keycloak_id,
            timestamp_servidor__gte=inicio,
            timestamp_servidor__lte=fin,
            punto_control_id__in=punto_ids,
        )
        .values_list("punto_control_id", flat=True)
    )
    puntos = [
        {"id": s.punto_control_id, "nombre": s.punto_control.nombre, "hecho": s.punto_control_id in completados}
        for s in secuencia
    ]
    return {
        "total": len(punto_ids),
        "escaneados": len(completados),
        "puntos": puntos,
        "punto_ids": punto_ids,
    }


@login_required
@requiere_instalacion
@solo_super_admin
def index(request):
    """Pantalla del simulador. La ronda se decide por hora al iniciar (ver iniciar)."""
    return render(request, "escaner/escaner.html")


@login_required
@require_POST
def iniciar(request):
    """Inicia la ejecución de la ronda que corresponde a la HORA ACTUAL del
    servidor (zona Santiago). Solo super_admin. No se elige Día/Noche a mano."""
    if not permisos.es_super_admin(request):
        return JsonResponse({"ok": False, "error": "No autorizado."}, status=403)

    instalacion_id = request.session.get("instalacion_id")
    if not instalacion_id:
        return JsonResponse({"ok": False, "error": "Selecciona una instalación primero."}, status=400)

    ronda = _ronda_para_ahora(instalacion_id)
    if ronda is None:
        return JsonResponse({"ok": False, "error": "No hay ronda activa en este horario."}, status=404)

    keycloak_id = getattr(request.user, "keycloak_id", "") or ""
    ventana = _ventana_turno(ronda, timezone.now())
    inicio, fin = ventana

    # Reutilizar la ejecución del MISMO guardia + MISMA ronda iniciada dentro de
    # la ventana del turno actual (retoma el progreso); si no hay, crear una.
    ejecucion = (
        RondaEjecucion.objects
        .filter(
            ronda=ronda,
            guardia_keycloak_id=keycloak_id,
            estado=RondaEjecucion.Estado.EN_CURSO,
            iniciada_en__gte=inicio,
            iniciada_en__lte=fin,
        )
        .order_by("-iniciada_en")
        .first()
    )
    if ejecucion is None:
        ejecucion = RondaEjecucion.objects.create(
            ronda=ronda,
            guardia_keycloak_id=keycloak_id,
            instalacion_id=instalacion_id,
            estado=RondaEjecucion.Estado.EN_CURSO,
        )

    estado = _estado_ejecucion(ejecucion, ventana)
    return JsonResponse({
        "ok": True,
        "ronda": ronda.nombre,
        "progreso": {"escaneados": estado["escaneados"], "total": estado["total"], "puntos": estado["puntos"]},
    })


@login_required
@require_POST
def registrar(request):
    """
    Registra un escaneo en libro_novedades (igual que antes) y, si hay una
    ejecución en curso, lo asocia a esa ronda y devuelve el progreso. Solo
    super_admin (403 JSON si no).
    """
    if not permisos.es_super_admin(request):
        return JsonResponse({"ok": False, "error": "No autorizado."}, status=403)

    qr_token = (request.POST.get("qr_token") or "").strip()
    if not qr_token:
        return JsonResponse({"ok": False, "error": "No se recibió ningún código."}, status=400)

    lat = _parse_coord(request.POST.get("lat"))
    lng = _parse_coord(request.POST.get("lng"))
    if lat is None or lng is None:
        return JsonResponse(
            {"ok": False, "error": "Falta la ubicación: debes permitir el GPS para registrar."},
            status=400,
        )

    texto = (request.POST.get("texto") or "").strip() or None
    keycloak_id = getattr(request.user, "keycloak_id", "") or ""
    ahora = timezone.now()

    # Ejecución en curso (si la hay): sus escaneos se etiquetan con su ronda_id.
    # La ventana del turno se ancla a cuándo se inició esa ejecución.
    ejecucion = _ejecucion_en_curso(keycloak_id)
    ronda_id_evento = ejecucion.ronda_id if ejecucion else None
    ventana = _ventana_turno(ejecucion.ronda, ejecucion.iniciada_en) if ejecucion else None

    cp = PuntoControl.objects.filter(qr_token=qr_token, activo=True).first()

    with transaction.atomic():
        if cp is None:
            tipo_ne = TipoEvento.objects.filter(codigo="codigo_no_existe").first()
            if tipo_ne:
                LibroNovedades.objects.create(
                    instalacion_id=0,  # desconocido (no hay punto); sin FK
                    ronda_id=ronda_id_evento,
                    guardia_keycloak_id=keycloak_id,
                    tipo_evento=tipo_ne,
                    timestamp_evento=ahora,
                    timestamp_servidor=ahora,
                    lat=lat,
                    lng=lng,
                    estado="error",
                    texto=texto or f"QR escaneado sin coincidencia: {qr_token}",
                )
            return JsonResponse({"ok": False, "error": "Código no existe."}, status=404)

        # Bloqueo de re-escaneo POR GUARDIA + TURNO: si ESTE guardia ya registró
        # ESTE punto, para ESTA ronda, DENTRO de la ventana del turno, no se
        # registra de nuevo (solo se avisa). Independiente de la sesión/pantalla.
        if ejecucion and ventana and LibroNovedades.objects.filter(
            ronda_id=ejecucion.ronda_id,
            guardia_keycloak_id=keycloak_id,
            punto_control=cp,
            timestamp_servidor__gte=ventana[0],
            timestamp_servidor__lte=ventana[1],
        ).exists():
            estado = _estado_ejecucion(ejecucion, ventana)
            return JsonResponse({
                "ok": False,
                "ya_escaneado": True,
                "error": "Ya escaneaste este punto en esta ronda.",
                "checkpoint": cp.nombre,
                "progreso": {
                    "escaneados": estado["escaneados"],
                    "total": estado["total"],
                    "puntos": estado["puntos"],
                },
            })

        # Distancia del celular al punto (siempre, para auditoría). Acotada al
        # máximo del campo (decimal 7,2 -> 99999.99 m) por seguridad.
        distancia_m = _haversine_m(lat, lng, float(cp.lat), float(cp.lng))
        distancia_dec = min(Decimal(f"{distancia_m:.2f}"), Decimal("99999.99"))

        if not cp.validar_posicion:
            codigo_tipo = "arribo_sin_geo"
            dentro_geocerca = None
        elif distancia_m <= cp.tolerancia_mts:
            codigo_tipo = "arribo"
            dentro_geocerca = True
        else:
            codigo_tipo = "arribo_invalido"
            dentro_geocerca = False

        tipo_evento = TipoEvento.objects.filter(codigo=codigo_tipo).first()
        if tipo_evento is None:
            return JsonResponse(
                {"ok": False, "error": "Falta el catálogo de eventos (corre seed_tipos_evento)."},
                status=500,
            )

        LibroNovedades.objects.create(
            instalacion_id=cp.instalacion_id,
            ronda_id=ronda_id_evento,             # NUEVO: ronda en curso (o null)
            punto_control=cp,
            guardia_keycloak_id=keycloak_id,
            tipo_evento=tipo_evento,
            timestamp_evento=ahora,
            timestamp_servidor=ahora,
            lat=lat,
            lng=lng,
            distancia_metros=distancia_dec,
            dentro_geocerca=dentro_geocerca,
            estado="ok",
            texto=texto,
        )

    resp = {
        "ok": True,
        "checkpoint": cp.nombre,
        # En BD se guarda con USE_TZ (UTC). Para MOSTRAR: localtime -> Santiago, HH:MM:SS.
        "hora": timezone.localtime(ahora).strftime("%H:%M:%S"),
        "distancia_metros": float(distancia_dec),
        "dentro_geocerca": dentro_geocerca,
    }

    # Progreso de la ronda en curso (si la hay), dentro de la ventana del turno.
    if ejecucion and ventana:
        estado = _estado_ejecucion(ejecucion, ventana)
        resp["pertenece"] = cp.id in estado["punto_ids"]
        resp["progreso"] = {
            "escaneados": estado["escaneados"],
            "total": estado["total"],
            "puntos": estado["puntos"],
        }
        completada = estado["total"] > 0 and estado["escaneados"] >= estado["total"]
        resp["completada"] = completada
        if completada and ejecucion.estado != RondaEjecucion.Estado.COMPLETADA:
            ejecucion.estado = RondaEjecucion.Estado.COMPLETADA
            ejecucion.finalizada_en = ahora
            ejecucion.save(update_fields=["estado", "finalizada_en"])

    return JsonResponse(resp)
