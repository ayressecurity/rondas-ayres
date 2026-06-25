"""
App 'escaner' — SIMULADOR DE EJECUCIÓN DE RONDA (testeo de la futura app móvil).
SOLO super_admin, dentro de la instalación seleccionada.

Estas vistas son ADAPTADORES WEB DELGADOS: resuelven permisos/sesión/parseo y
serializan a JSON, pero TODA la lógica de negocio (haversine, tipo_evento,
bloqueo de re-escaneo, INSERT en libro_novedades, manejo de ronda_ejecucion) vive
en el service compartido apps/comun/services/rondas.py, que web y API usan por
igual. El comportamiento del escáner web es idéntico al anterior.

CONTRATO DE IDENTIDAD: request.user.keycloak_id es un UUID (en BD va sin guiones);
str() lo deja CON guiones, que es exactamente lo que se escribía antes en
libro_novedades/ronda_ejecucion. Ese string con guiones es lo que se pasa al service.
"""
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.comun.decoradores import requiere_instalacion, solo_super_admin
from apps.comun.services.rondas import (
    SinRondaActiva,
    _parse_coord,
    iniciar_o_reusar_ejecucion,
    registrar_escaneo,
)
from apps.cuentas import permisos


def _guardia_con_guiones(request):
    """keycloak_id del usuario como STRING CON GUIONES (lo que se escribe en BD).

    keycloak_id es un UUIDField (sin guiones en BD); str(UUID) lo devuelve con
    guiones, idéntico al valor que se guardaba antes en libro_novedades."""
    return str(getattr(request.user, "keycloak_id", "") or "")


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

    try:
        ejecucion, _ventana, estado = iniciar_o_reusar_ejecucion(
            instalacion_id=instalacion_id,
            guardia_keycloak_id=_guardia_con_guiones(request),
            ahora=timezone.now(),
        )
    except SinRondaActiva:
        return JsonResponse({"ok": False, "error": "No hay ronda activa en este horario."}, status=404)

    return JsonResponse({
        "ok": True,
        "ronda": ejecucion.ronda.nombre,
        "progreso": {"escaneados": estado["escaneados"], "total": estado["total"], "puntos": estado["puntos"]},
    })


@login_required
@require_POST
def registrar(request):
    """Registra un escaneo en libro_novedades vía el service y devuelve el
    progreso. Solo super_admin (403 JSON si no). El parseo del POST (qr_token,
    GPS) se valida aquí; el resto lo decide el service."""
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

    res = registrar_escaneo(
        instalacion_id=request.session.get("instalacion_id"),
        guardia_keycloak_id=_guardia_con_guiones(request),
        qr_token=qr_token,
        lat=lat,
        lng=lng,
        texto=texto,
        ahora=timezone.now(),
    )

    # Traducción del resultado del service a la MISMA respuesta JSON de antes.
    resultado = res["resultado"]
    if resultado == "codigo_no_existe":
        return JsonResponse({"ok": False, "error": "Código no existe."}, status=404)
    if resultado == "catalogo_incompleto":
        return JsonResponse(
            {"ok": False, "error": "Falta el catálogo de eventos (corre seed_tipos_evento)."},
            status=500,
        )
    if resultado == "ya_escaneado":
        return JsonResponse({
            "ok": False,
            "ya_escaneado": True,
            "error": "Ya escaneaste este punto en esta ronda.",
            "checkpoint": res["checkpoint"],
            "progreso": res["progreso"],
        })

    # resultado == "ok"
    salida = {
        "ok": True,
        "checkpoint": res["checkpoint"],
        "hora": res["hora"],
        "distancia_metros": res["distancia_metros"],
        "dentro_geocerca": res["dentro_geocerca"],
    }
    for clave in ("pertenece", "progreso", "completada"):
        if clave in res:
            salida[clave] = res[clave]
    return JsonResponse(salida)
