"""Vistas de la app 'rondas'. Placeholder navegable dentro de una instalacion.

Incluye el ESCÁNER DE PRUEBA (QR): herramienta de testeo SOLO para super_admin
que lee el qr_token de un punto_control con la cámara y registra el escaneo en
libro_novedades. Es una pre-versión para validar el flujo de escaneo de la app.
"""
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


@login_required
@requiere_instalacion
def index(request):
    return render(request, "rondas/index.html")


@login_required
@solo_super_admin
def escaner(request):
    """Pantalla del escáner de prueba (cámara + lector QR). Solo super_admin."""
    return render(request, "rondas/escaner.html")


@login_required
@require_POST
def escaner_registrar(request):
    """
    Recibe el qr_token leído por la cámara y registra el escaneo en
    libro_novedades. Solo super_admin (validado en línea para responder 403 JSON).

    - Sin punto activo con ese token -> registra un evento 'codigo_no_existe'
      (trazabilidad) y responde error.
    - Con punto activo -> registra un 'arribo' con el guardia = sub del usuario.
    Todo dentro de una transacción.
    """
    if not permisos.es_super_admin(request):
        return JsonResponse({"ok": False, "error": "No autorizado."}, status=403)

    qr_token = (request.POST.get("qr_token") or "").strip()
    if not qr_token:
        return JsonResponse({"ok": False, "error": "No se recibió ningún código."}, status=400)

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
                    estado="error",
                    texto=f"QR escaneado sin coincidencia: {qr_token}",
                )
            return JsonResponse({"ok": False, "error": "Código no existe."}, status=404)

        tipo_arribo = TipoEvento.objects.filter(codigo="arribo").first()
        if tipo_arribo is None:
            return JsonResponse(
                {"ok": False, "error": "Falta el catálogo de eventos (corre seed_tipos_evento)."},
                status=500,
            )

        LibroNovedades.objects.create(
            instalacion_id=cp.instalacion_id,
            punto_control=cp,
            guardia_keycloak_id=keycloak_id,
            tipo_evento=tipo_arribo,
            timestamp_evento=ahora,
            timestamp_servidor=ahora,
            estado="ok",
        )

    # En BD se guarda con USE_TZ (UTC). Para MOSTRAR: localtime -> Santiago de
    # Chile (TIME_ZONE) y formato HH:MM:SS, sin microsegundos/decimales.
    return JsonResponse({
        "ok": True,
        "checkpoint": cp.nombre,
        "hora": timezone.localtime(ahora).strftime("%H:%M:%S"),
    })
