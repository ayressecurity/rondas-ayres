"""
App 'escaner' — ESCÁNER DE PRUEBA (QR). Herramienta de testeo SOLO para
super_admin: lee el qr_token de un punto_control con la cámara + GPS del celular
y registra el escaneo en libro_novedades, calculando distancia a la geocerca.
No tiene modelos propios (solo escribe en novedades).

Pre-versión para validar el flujo de escaneo de la app móvil.
"""
from decimal import Decimal, InvalidOperation
from math import asin, cos, radians, sin, sqrt

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.checkpoints.models import PuntoControl
from apps.comun.decoradores import solo_super_admin
from apps.cuentas import permisos
from apps.novedades.models import LibroNovedades, TipoEvento

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


@login_required
@solo_super_admin
def index(request):
    """Pantalla del escáner de prueba (cámara + GPS + lector QR). Solo super_admin."""
    return render(request, "escaner/escaner.html")


@login_required
@require_POST
def registrar(request):
    """
    Recibe qr_token + lat/lng (del celular, OBLIGATORIOS) + texto (opcional) y
    registra el escaneo en libro_novedades. Solo super_admin (403 JSON si no).

    - Faltan lat/lng -> error, no registra.
    - Sin punto activo con ese token -> registra 'codigo_no_existe' y responde error.
    - Con punto activo -> SIEMPRE guarda lat/lng del celular y la distancia
      (haversine) al punto. Si el punto valida posición: tipo 'arribo' y
      dentro_geocerca = (distancia <= tolerancia). Si no valida: tipo
      'arribo_sin_geo' y dentro_geocerca = null (pero igual guarda lat/lng/dist).
    Todo dentro de una transacción.
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

    cp = PuntoControl.objects.filter(qr_token=qr_token, activo=True).first()

    with transaction.atomic():
        if cp is None:
            # Código sin coincidencia: dejamos rastro con tipo 'codigo_no_existe'.
            tipo_ne = TipoEvento.objects.filter(codigo="codigo_no_existe").first()
            if tipo_ne:
                LibroNovedades.objects.create(
                    instalacion_id=0,  # desconocido (no hay punto); sin FK
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

        # Distancia del celular al punto (siempre, para auditoría). La geocerca
        # se evalúa con la distancia real; al GUARDAR la acotamos al máximo del
        # campo (decimal 7,2 -> 99999.99 m) para no romper si el GPS está lejísimos.
        distancia_m = _haversine_m(lat, lng, float(cp.lat), float(cp.lng))
        distancia_dec = min(Decimal(f"{distancia_m:.2f}"), Decimal("99999.99"))

        if cp.validar_posicion:
            codigo_tipo = "arribo"
            dentro_geocerca = distancia_m <= cp.tolerancia_mts
        else:
            codigo_tipo = "arribo_sin_geo"
            dentro_geocerca = None  # no se evalúa la geocerca

        tipo_evento = TipoEvento.objects.filter(codigo=codigo_tipo).first()
        if tipo_evento is None:
            return JsonResponse(
                {"ok": False, "error": "Falta el catálogo de eventos (corre seed_tipos_evento)."},
                status=500,
            )

        LibroNovedades.objects.create(
            instalacion_id=cp.instalacion_id,
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

    # En BD se guarda con USE_TZ (UTC). Para MOSTRAR: localtime -> Santiago de
    # Chile (TIME_ZONE) y formato HH:MM:SS, sin microsegundos/decimales.
    return JsonResponse({
        "ok": True,
        "checkpoint": cp.nombre,
        "hora": timezone.localtime(ahora).strftime("%H:%M:%S"),
        "distancia_metros": float(distancia_dec),
        "dentro_geocerca": dentro_geocerca,
    })
