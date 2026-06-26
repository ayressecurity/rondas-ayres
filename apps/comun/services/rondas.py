"""
SERVICE COMPARTIDO de ejecución de rondas (orquestación pura, sin web).

Única fuente de verdad para el escaneo de puntos: hoy la usa el escáner web
(apps/escaner/views.py, super_admin) y mañana la usará la API móvil. Aquí NO hay
request, session ni JsonResponse: solo datos de entrada y de salida (dicts/tuplas).

Esta lógica fue MOVIDA tal cual desde apps/escaner/views.py (refactor "mover, no
reescribir"): mismo cálculo haversine, misma decisión de tipo_evento, mismo
bloqueo de re-escaneo, mismos INSERT en libro_novedades y mismo manejo de
ronda_ejecucion. El comportamiento es idéntico al anterior.

CONTRATO DE IDENTIDAD (crítico): `guardia_keycloak_id` entra y se escribe TAL CUAL
(STRING CON GUIONES) en libro_novedades y ronda_ejecucion. La normalización (quitar
guiones) es SOLO para casar contra cuentas_usuario y vive en apps/cuentas/identidad.py;
el service NUNCA normaliza al escribir. Eso mantiene el bloqueo de re-escaneo
consistente entre web y API.

Zona horaria: todo en America/Santiago vía servidor (timezone.now()/localtime).
"""
from datetime import datetime, timedelta
from decimal import Decimal
from math import asin, cos, radians, sin, sqrt

from django.db import transaction
from django.utils import timezone

from apps.checkpoints.models import PuntoControl
from apps.escaner.models import RondaEjecucion
from apps.novedades.models import LibroNovedades, TipoEvento
from apps.rondas.models import EstadoGenerico, Ronda, RondaSecuencia

# Radio medio de la Tierra en metros (para haversine).
RADIO_TIERRA_M = 6_371_000


# --------------------------------------------------------------------------- #
# Errores del service (el adaptador web/API los traduce a su respuesta).       #
# --------------------------------------------------------------------------- #
class SinRondaActiva(Exception):
    """No hay ninguna ronda activa cuyo rango horario contenga la hora actual."""


# --------------------------------------------------------------------------- #
# Helpers PUROS (movidos desde escaner/views.py, sin cambios de comportamiento) #
# --------------------------------------------------------------------------- #
def _haversine_m(lat1, lng1, lat2, lng2):
    """Distancia en metros entre dos puntos (lat/lng en grados) por haversine."""
    lat1, lng1, lat2, lng2 = map(radians, (lat1, lng1, lat2, lng2))
    dlat = lat2 - lat1
    dlng = lng2 - lng1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlng / 2) ** 2
    return 2 * RADIO_TIERRA_M * asin(sqrt(a))


def _parse_coord(valor):
    """Convierte el string a float; None si falta o no es válido."""
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
    """Ronda activa DE ESTA INSTALACIÓN cuyo rango horario contiene la hora actual
    de Santiago (hora del SERVIDOR, no del navegador). None si ninguna aplica.

    SIEMPRE filtra por instalacion_id: es imposible que devuelva una ronda de otra
    instalación, aunque exista otra ronda con el MISMO nombre y horario en otra
    instalación.

    Desempate determinista (caso secundario): si dos rondas de la MISMA
    instalación solapan horario, se elige la de mayor id (la creada más
    recientemente). En el flujo normal (Día/Noche sin solape) solo una calza."""
    ahora = timezone.localtime(timezone.now()).time()
    candidatas = [
        r for r in Ronda.objects.filter(
            instalacion_id=instalacion_id,
            estado=EstadoGenerico.ACTIVA,
            hora_inicio__isnull=False,
            hora_fin__isnull=False,
        )
        if _en_rango(ahora, r.hora_inicio, r.hora_fin)
    ]
    return max(candidatas, key=lambda r: r.id) if candidatas else None


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


def _ejecucion_en_curso(guardia_keycloak_id, instalacion_id):
    """Última ronda_ejecucion en curso del guardia EN ESTA INSTALACIÓN (o None).

    Filtra SIEMPRE por instalacion_id: si el mismo guardia tiene ejecuciones en
    curso en VARIAS instalaciones (caso super_admin), jamás se devuelve la de otra
    instalación. Sin este filtro, se elegía la ejecución más reciente del guardia
    aunque fuera de OTRA instalación, registrando el evento contra la ronda
    equivocada (ese era el bug de cruce entre instalaciones)."""
    return (
        RondaEjecucion.objects
        .select_related("ronda")  # _ventana_turno lee ejecucion.ronda sin query extra
        .filter(
            guardia_keycloak_id=guardia_keycloak_id,
            instalacion_id=instalacion_id,
            estado=RondaEjecucion.Estado.EN_CURSO,
        )
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


# --------------------------------------------------------------------------- #
# Orquestación (lo que antes vivía inline en las vistas iniciar/registrar).     #
# --------------------------------------------------------------------------- #
def iniciar_o_reusar_ejecucion(*, instalacion_id, guardia_keycloak_id, ahora):
    """Inicia (o retoma) la ejecución de la ronda que corresponde a la HORA actual.

    Devuelve (ejecucion, ventana, estado). La ronda se decide por hora del
    servidor; si ninguna aplica -> SinRondaActiva (el adaptador la traduce a 404).

    Reusa la ejecución del MISMO guardia + MISMA ronda iniciada dentro de la
    ventana del turno (retoma el progreso); si no hay, crea una. Idéntico a la
    lógica anterior de la vista `iniciar`.
    """
    ronda = _ronda_para_ahora(instalacion_id)
    if ronda is None:
        raise SinRondaActiva()

    ventana = _ventana_turno(ronda, ahora)
    inicio, fin = ventana

    ejecucion = (
        RondaEjecucion.objects
        .select_related("ronda")  # para leer ejecucion.ronda.nombre sin query extra
        .filter(
            ronda=ronda,
            guardia_keycloak_id=guardia_keycloak_id,  # CON guiones, tal cual
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
            guardia_keycloak_id=guardia_keycloak_id,  # CON guiones, tal cual
            instalacion_id=instalacion_id,
            estado=RondaEjecucion.Estado.EN_CURSO,
        )

    estado = _estado_ejecucion(ejecucion, ventana)
    return ejecucion, ventana, estado


def registrar_escaneo(*, instalacion_id, guardia_keycloak_id, qr_token, lat, lng, texto, ahora,
                      timestamp_evento=None):
    """Registra un escaneo de QR en libro_novedades y devuelve el resultado (dict).

    TIEMPOS (decisión QA #5):
      - `ahora` = hora REAL del servidor (Santiago) al registrar -> timestamp_servidor.
      - `timestamp_evento` (opcional) = hora de TERRENO (offline). Si no viene, se
        usa `ahora`. La WEB no lo pasa -> ambos quedan ~now() (comportamiento igual
        que antes). Solo la API con hora de terreno los diferencia. El bloqueo de
        re-escaneo SIEMPRE filtra la ventana por timestamp_servidor (hora real).

    `instalacion_id` es la instalación de contexto (la del llamador). NOTA: para
    preservar EXACTAMENTE el comportamiento previo, el INSERT del arribo usa la
    instalación del PROPIO punto (cp.instalacion_id) y el de "código no existe"
    usa 0 (punto desconocido); por eso este parámetro queda reservado para
    futuros llamadores (API) y no altera lo que escribe el escáner web.

    El dict siempre trae "resultado" como discriminador para el adaptador:
      - "codigo_no_existe": el QR no calza con ningún punto activo (se registró el
        evento codigo_no_existe igual que antes). -> el adaptador responde 404.
      - "punto_otra_instalacion": el punto existe pero es de OTRA instalación; NO
        se registra nada ni se toca ronda_ejecucion. -> el adaptador da error claro.
      - "sin_ronda_activa": no hay ninguna ronda activa cuyo horario contenga la
        hora actual en esa instalación; NO se registra nada. -> error claro.
      - "catalogo_incompleto": falta el catálogo (corre seed_tipos_evento). -> 500.
      - "ya_escaneado": el guardia ya registró ese punto en esa ronda+ventana.
      - "ok": arribo registrado; incluye progreso y completada si hay ejecución.

    Se devuelven dicts (no se lanza excepción) en codigo_no_existe para que el
    INSERT de ese evento SE CONFIRME (igual que la versión anterior, que escribía
    y luego respondía 404).
    """
    # Ejecución en curso (si la hay): sus escaneos se etiquetan con su ronda_id.
    # La ventana del turno se ancla a cuándo se inició esa ejecución. SIEMPRE
    # acotada a la instalación de operación (instalacion_id), para que nunca se
    # resuelva la ronda/ejecución de OTRA instalación.
    ejecucion = _ejecucion_en_curso(guardia_keycloak_id, instalacion_id)
    ronda_id_evento = ejecucion.ronda_id if ejecucion else None
    ventana = _ventana_turno(ejecucion.ronda, ejecucion.iniciada_en) if ejecucion else None

    # Tiempos: servidor = ahora (real); terreno = el de offline o ahora si no vino.
    ts_servidor = ahora
    ts_evento = timestamp_evento or ahora

    cp = PuntoControl.objects.filter(qr_token=qr_token, activo=True).first()

    with transaction.atomic():
        if cp is None:
            tipo_ne = TipoEvento.objects.filter(codigo="codigo_no_existe").first()
            if tipo_ne:
                LibroNovedades.objects.create(
                    instalacion_id=0,  # desconocido (no hay punto); sin FK
                    ronda_id=ronda_id_evento,
                    guardia_keycloak_id=guardia_keycloak_id,
                    tipo_evento=tipo_ne,
                    timestamp_evento=ts_evento,
                    timestamp_servidor=ts_servidor,
                    lat=lat,
                    lng=lng,
                    estado="error",
                    texto=texto or f"QR escaneado sin coincidencia: {qr_token}",
                )
            return {"resultado": "codigo_no_existe"}

        # El punto debe pertenecer a la MISMA instalación que se está operando.
        # Si es de OTRA instalación, NO se registra nada (evita marcar puntos
        # ajenos como "fuera de geocerca" a miles de metros). Comparación
        # type-safe (la sesión guarda int; un futuro llamador podría dar str).
        if str(cp.instalacion_id) != str(instalacion_id):
            return {"resultado": "punto_otra_instalacion", "punto_nombre": cp.nombre}

        # Debe existir una ronda ACTIVA cuyo horario contenga la hora actual en
        # esta instalación. Si no, NO se registra nada (decisión QA #8): no se
        # permite marcar fuera de ventana de ronda (evita duplicados sin turno).
        if _ronda_para_ahora(cp.instalacion_id) is None:
            return {"resultado": "sin_ronda_activa", "punto_nombre": cp.nombre}

        # Bloqueo de re-escaneo POR GUARDIA + TURNO: si ESTE guardia ya registró
        # ESTE punto, para ESTA ronda, DENTRO de la ventana del turno, no se
        # registra de nuevo (solo se avisa).
        if ejecucion and ventana and LibroNovedades.objects.filter(
            ronda_id=ejecucion.ronda_id,
            guardia_keycloak_id=guardia_keycloak_id,
            punto_control=cp,
            timestamp_servidor__gte=ventana[0],
            timestamp_servidor__lte=ventana[1],
        ).exists():
            estado = _estado_ejecucion(ejecucion, ventana)
            return {
                "resultado": "ya_escaneado",
                "checkpoint": cp.nombre,
                "progreso": {
                    "escaneados": estado["escaneados"],
                    "total": estado["total"],
                    "puntos": estado["puntos"],
                },
            }

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
            return {"resultado": "catalogo_incompleto"}

        evento = LibroNovedades.objects.create(
            instalacion_id=cp.instalacion_id,
            ronda_id=ronda_id_evento,             # ronda en curso (o null)
            punto_control=cp,
            guardia_keycloak_id=guardia_keycloak_id,
            tipo_evento=tipo_evento,
            timestamp_evento=ts_evento,
            timestamp_servidor=ts_servidor,
            lat=lat,
            lng=lng,
            distancia_metros=distancia_dec,
            dentro_geocerca=dentro_geocerca,
            estado="ok",
            texto=texto,
        )

    resp = {
        "resultado": "ok",
        # id y código del evento creado (los usa la API para el 201; el adaptador
        # web simplemente los ignora -> su comportamiento no cambia).
        "libro_id": evento.id,
        "tipo_evento": tipo_evento.codigo,
        "checkpoint": cp.nombre,
        # En BD se guarda con USE_TZ (UTC). Para MOSTRAR: localtime -> Santiago.
        "hora": timezone.localtime(ahora).strftime("%H:%M:%S"),
        "distancia_metros": float(distancia_dec),
        "dentro_geocerca": dentro_geocerca,
    }

    # Progreso de la ronda en curso (si la hay), dentro de la ventana del turno.
    # (Igual que antes: el marcar "completada" ocurre FUERA de la transacción.)
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

    return resp
