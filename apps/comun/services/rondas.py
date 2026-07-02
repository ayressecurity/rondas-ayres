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
from datetime import datetime, time, timedelta
from decimal import Decimal
from math import asin, cos, radians, sin, sqrt

from django.db import transaction
from django.utils import timezone

from apps.checkpoints.models import PuntoControl
from apps.escaner.models import RondaEjecucion
from apps.novedades.models import LibroNovedades, TipoEvento
from apps.rondas.models import EstadoGenerico, ProgramacionHorario, Ronda, RondaSecuencia

# Sentinela: el escaneo cae fuera de toda ventana de alarma (antes de la primera
# alarma o en un hueco). El service lo traduce a resultado "sin_ventana_activa".
SIN_VENTANA = object()

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


def _ronda_para_momento(instalacion_id, momento):
    """Ronda activa DE ESTA INSTALACIÓN cuyo rango horario contiene la hora local
    (Santiago) de `momento` (un datetime aware). None si ninguna aplica.

    SIEMPRE filtra por instalacion_id: es imposible que devuelva una ronda de otra
    instalación, aunque exista otra ronda con el MISMO nombre y horario en otra
    instalación.

    Desempate determinista (caso secundario): si dos rondas de la MISMA
    instalación solapan horario, se elige la de mayor id (la creada más
    recientemente). En el flujo normal (Día/Noche sin solape) solo una calza."""
    hora = timezone.localtime(momento).time()
    candidatas = [
        r for r in Ronda.objects.filter(
            instalacion_id=instalacion_id,
            estado=EstadoGenerico.ACTIVA,
            hora_inicio__isnull=False,
            hora_fin__isnull=False,
        )
        if _en_rango(hora, r.hora_inicio, r.hora_fin)
    ]
    return max(candidatas, key=lambda r: r.id) if candidatas else None


def _ronda_para_ahora(instalacion_id):
    """Ronda activa cuyo rango horario contiene la hora actual del SERVIDOR
    (Santiago). Thin-wrapper de _ronda_para_momento con `timezone.now()`:
    comportamiento idéntico al anterior (lo usa el escaneo online)."""
    return _ronda_para_momento(instalacion_id, timezone.now())


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


def ventanas_de_alarma(ronda, ref, horarios=None):
    """TODAS las ventanas de marcaje de la ronda dentro del turno que contiene `ref`.

    Devuelve una lista ORDENADA por datetime: [(horario, inicio, fin), ...], donde
    cada alarma (programacion_horario, de programaciones ACTIVAS) abre una ventana:
    inicio = la alarma; fin = (siguiente alarma − 1s) o hora_fin del turno si es la
    última. `horario` es el ProgramacionHorario que abre la ventana.

    Cada alarma (hora, minuto) se ancla a un datetime real dentro del turno,
    resolviendo el cruce de medianoche (se prueba el día de inicio y el siguiente).
    Si varias programaciones aportan la MISMA hora, se fusiona en una sola ventana
    (se conserva la 1ª por orden de captura). Todo en zona Santiago.

    Devuelve [] si la ronda no tiene rango horario o no tiene alarmas; el llamador
    decide el fallback (turno completo). `ref` es un datetime aware.

    `horarios` (opcional): lista de ProgramacionHorario ya cargada (p.ej. desde un
    prefetch) para EVITAR N+1 al serializar muchas rondas. Si es None, se consulta.
    """
    turno = _ventana_turno(ronda, ref)
    if turno is None:                 # ronda sin rango horario (no debería ocurrir)
        return []
    turno_inicio, turno_fin = turno

    if horarios is None:
        horarios = list(
            ProgramacionHorario.objects
            .filter(programacion__ronda=ronda, programacion__activo=True)
        )
    # Orden determinista de captura (para elegir representante ante alarmas iguales).
    horarios = sorted(horarios, key=lambda h: (h.orden, h.id))
    if not horarios:
        return []

    # Ancla cada alarma a su datetime dentro del turno (cruce de medianoche).
    un_dia = timedelta(days=1)
    anclados = []  # [(dt, horario), ...]
    for h in horarios:
        t = time(h.hora, h.minuto)
        for dia in (turno_inicio.date(), turno_inicio.date() + un_dia):
            cand = _aware(dia, t)
            if turno_inicio <= cand <= turno_fin:
                anclados.append((cand, h))
                break
    if not anclados:                  # todas fuera del turno (el form ya lo evita)
        return []

    # Orden por datetime + fusión de alarmas de igual datetime (1ª por captura).
    anclados.sort(key=lambda par: par[0])
    unicos = []
    vistos = set()
    for dt, h in anclados:
        if dt not in vistos:
            vistos.add(dt)
            unicos.append((dt, h))

    # Cada ventana: [alarma, siguiente alarma − 1s] o turno_fin si es la última.
    ventanas = []
    for i, (dt, h) in enumerate(unicos):
        fin = (unicos[i + 1][0] - timedelta(seconds=1)) if i + 1 < len(unicos) else turno_fin
        ventanas.append((h, dt, fin))
    return ventanas


def _ventana_alarma(ronda, ref):
    """Sub-ventana de marcaje VIGENTE según las ALARMAS (la que contiene `ref`).

    Thin-consumer de `ventanas_de_alarma` (misma lógica de anclaje y cruce de
    medianoche, sin duplicar):
    - FALLBACK: si la ronda NO tiene alarmas -> ventana de turno completa
      (_ventana_turno): comportamiento idéntico al anterior.
    - Con alarmas: (inicio, fin) de la ventana que contiene `ref` (inicio = última
      alarma <= ref; fin = siguiente alarma − 1s o turno_fin si es la última).
    - Si `ref` es ANTERIOR a la primera alarma (hueco al inicio del turno) -> SIN_VENTANA.

    `ref` es un datetime aware.
    """
    turno = _ventana_turno(ronda, ref)
    if turno is None:                 # ronda sin rango horario (no debería ocurrir)
        return SIN_VENTANA

    ventanas = ventanas_de_alarma(ronda, ref)
    if not ventanas:
        return turno                  # FALLBACK: una marca por turno (como hoy)

    ref = timezone.localtime(ref)
    if ref < ventanas[0][1]:          # antes de la primera alarma -> hueco
        return SIN_VENTANA

    # Última ventana cuya alarma de inicio ya pasó (<= ref).
    activa = ventanas[0]
    for ventana in ventanas:
        if ventana[1] <= ref:
            activa = ventana
        else:
            break
    return (activa[1], activa[2])


def _estado_ejecucion(ronda_id, guardia_keycloak_id, ventana, campo_tiempo="timestamp_servidor"):
    """Progreso dentro de la VENTANA (de alarma o de turno): puntos en orden +
    cuáles ya escaneó ESE guardia para ESA ronda dentro de [inicio, fin].

    total = puntos en ronda_secuencia. escaneados = puntos DISTINTOS registrados
    por EL guardia en la ventana (se lee SIEMPRE de libro_novedades, nunca de
    memoria/sesión: así el progreso sobrevive a cierre de app/sesión/reinicio).
    El conteo es POR GUARDIA (cada guardia su propio avance), como hoy.

    `campo_tiempo` (default 'timestamp_servidor') = columna por la que se filtra la
    ventana. El escaneo online usa el default (comportamiento intacto). El arribo
    OFFLINE pasa 'timestamp_evento' porque su hora real (terreno) es la que cae en
    la ventana del turno; su timestamp_servidor es la hora de reenvío (posterior).
    """
    inicio, fin = ventana
    secuencia = (
        RondaSecuencia.objects
        .filter(ronda_id=ronda_id)
        .select_related("punto_control")
        .order_by("orden")
    )
    punto_ids = [s.punto_control_id for s in secuencia]
    completados = set(
        LibroNovedades.objects
        .filter(
            ronda_id=ronda_id,
            guardia_keycloak_id=guardia_keycloak_id,
            punto_control_id__in=punto_ids,
            **{f"{campo_tiempo}__gte": inicio, f"{campo_tiempo}__lte": fin},
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

    # La reutilización de la ejecución se ancla a la ventana del TURNO (la
    # ejecución es la "sesión" del turno: vive todo el turno, no se cierra por
    # ventana de alarma). El progreso mostrado, en cambio, es el de la ventana
    # de alarma vigente (lo que el guardia realmente puede marcar ahora).
    ventana_turno = _ventana_turno(ronda, ahora)
    inicio, fin = ventana_turno

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

    # Progreso de la ventana de alarma vigente (o del turno si no hay alarmas /
    # si aún no entra la primera alarma, solo para mostrar).
    va = _ventana_alarma(ronda, ahora)
    ventana_estado = va if va is not SIN_VENTANA else ventana_turno
    estado = _estado_ejecucion(ronda.id, guardia_keycloak_id, ventana_estado)
    return ejecucion, ventana_turno, estado


def registrar_escaneo(*, instalacion_id, guardia_keycloak_id, qr_token, lat, lng, texto, ahora,
                      timestamp_evento=None, dispositivo_id=None):
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

    `dispositivo_id` (opcional) = id del Dispositivo enrolado que envió la marca
    (Fase 4). Solo lo pasa la API cuando llega un X-Device-Token válido; el escáner
    web NO lo pasa -> queda None -> el INSERT escribe NULL, idéntico a hoy.

    El dict siempre trae "resultado" como discriminador para el adaptador:
      - "codigo_no_existe": el QR no calza con ningún punto activo (se registró el
        evento codigo_no_existe igual que antes). -> el adaptador responde 404.
      - "punto_otra_instalacion": el punto existe pero es de OTRA instalación; NO
        se registra nada ni se toca ronda_ejecucion. -> el adaptador da error claro.
      - "sin_ronda_activa": no hay ninguna ronda activa cuyo horario contenga la
        hora actual en esa instalación; NO se registra nada. -> error claro.
      - "catalogo_incompleto": falta el catálogo (corre seed_tipos_evento). -> 500.
      - "sin_ventana_activa": la hora cae fuera de toda ventana de alarma (antes de
        la primera o en un hueco); NO se registra nada. -> error claro.
      - "ok": arribo registrado; incluye progreso y completada si hay ejecución. Se
        registra SIEMPRE que haya ventana activa, incluso si el punto ya se marcó en
        esa ventana (re-escaneo permitido: cada escaneo = una fila nueva). El
        progreso cuenta puntos ÚNICOS, así que re-escanear no lo infla.

    Se devuelven dicts (no se lanza excepción) en codigo_no_existe para que el
    INSERT de ese evento SE CONFIRME (igual que la versión anterior, que escribía
    y luego respondía 404).
    """
    # Tiempos: servidor = ahora (real); terreno = el de offline o ahora si no vino.
    ts_servidor = ahora
    ts_evento = timestamp_evento or ahora

    cp = PuntoControl.objects.filter(qr_token=qr_token, activo=True).first()

    with transaction.atomic():
        if cp is None:
            # No hay punto -> no se conoce la ronda; se estampa la ronda del
            # momento de la instalación de operación si la hay (informativo).
            ronda_ne = _ronda_para_ahora(instalacion_id) if instalacion_id else None
            tipo_ne = TipoEvento.objects.filter(codigo="codigo_no_existe").first()
            if tipo_ne:
                LibroNovedades.objects.create(
                    instalacion_id=0,  # desconocido (no hay punto); sin FK
                    ronda_id=ronda_ne.id if ronda_ne else None,
                    guardia_keycloak_id=guardia_keycloak_id,
                    dispositivo_id=dispositivo_id,  # None (web) -> NULL, igual que hoy
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

        # BLINDAJE (D.4): la ronda a usar/estampar se resuelve SIEMPRE por la hora
        # actual y la instalación del punto (no por ejecucion.ronda_id), para no
        # cruzar de turno/instalación. Si no hay ronda activa -> no se registra.
        ronda_actual = _ronda_para_ahora(cp.instalacion_id)
        if ronda_actual is None:
            return {"resultado": "sin_ronda_activa", "punto_nombre": cp.nombre}

        # Ventana de marcaje vigente: la de la ALARMA actual (o el turno completo
        # si la ronda no tiene alarmas). Si estamos fuera de toda ventana (antes
        # de la primera alarma o en un hueco) -> no se registra. Esta validación
        # de ventana SE MANTIENE: el re-escaneo solo aplica DENTRO de una ventana.
        ventana = _ventana_alarma(ronda_actual, ahora)
        if ventana is SIN_VENTANA:
            return {"resultado": "sin_ventana_activa", "punto_nombre": cp.nombre}

        # RE-ESCANEO PERMITIDO: aunque ESTE guardia ya haya marcado ESTE punto en
        # la ventana vigente, se registra igual (cada escaneo = una fila nueva). El
        # caso real: el SSPP pide una vuelta extra fuera del ciclo normal. El
        # progreso NO se infla porque _estado_ejecucion cuenta PUNTOS ÚNICOS
        # (set() de punto_control_id), así que re-escanear un punto no lo suma dos veces.

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
            ronda_id=ronda_actual.id,              # ronda del momento (blindaje)
            punto_control=cp,
            guardia_keycloak_id=guardia_keycloak_id,
            dispositivo_id=dispositivo_id,         # None (web) -> NULL, igual que hoy
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

    # Progreso del guardia en la VENTANA vigente (siempre desde libro_novedades).
    # NOTA: la ejecución (ronda_ejecucion) NO se auto-cierra por ventana (vive todo
    # el turno) para no romper el reinicio por alarma; "completada" es informativo.
    estado = _estado_ejecucion(ronda_actual.id, guardia_keycloak_id, ventana)
    resp["pertenece"] = cp.id in estado["punto_ids"]
    resp["progreso"] = {
        "escaneados": estado["escaneados"],
        "total": estado["total"],
        "puntos": estado["puntos"],
    }
    resp["completada"] = estado["total"] > 0 and estado["escaneados"] >= estado["total"]
    return resp


def registrar_evento_simple(*, instalacion_id, guardia_keycloak_id, codigo_tipo,
                            dispositivo_id=None, lat=None, lng=None, texto=None,
                            ahora, timestamp_evento=None, estado="ok", ronda_id=None):
    """Registra un evento SIMPLE en libro_novedades (SIN punto_control).

    Para eventos que no son arribos de ronda: sesión (sesion_inicio/sesion_fin),
    novedades del móvil, cancelación de ronda y otros de dispositivo. Reusa el
    MISMO patrón de INSERT que la rama `codigo_no_existe` de registrar_escaneo,
    pero como helper reutilizable — NO toca registrar_escaneo ni el marcaje.

    - Resuelve tipo_evento por `codigo_tipo`; si falta -> {"resultado":"catalogo_incompleto"}
      (el adaptador lo traduce a 503, igual que el resto).
    - `guardia_keycloak_id` se escribe TAL CUAL (CON guiones), como en todo el service.
    - `ronda_id` (opcional, default None): deja constancia de a QUÉ ronda refiere el
      evento (p.ej. ronda_cancelada). None -> NULL; retrocompatible con los
      llamadores que no lo pasan (sesión/novedad).
    - Devuelve {"resultado":"ok","libro_id":id,"tipo_evento":codigo_tipo}.
    """
    tipo = TipoEvento.objects.filter(codigo=codigo_tipo).first()
    if tipo is None:
        return {"resultado": "catalogo_incompleto"}

    evento = LibroNovedades.objects.create(
        instalacion_id=instalacion_id,
        ronda_id=ronda_id,
        guardia_keycloak_id=guardia_keycloak_id,
        dispositivo_id=dispositivo_id,
        tipo_evento=tipo,
        timestamp_evento=timestamp_evento or ahora,
        timestamp_servidor=ahora,
        lat=lat,
        lng=lng,
        estado=estado,
        texto=texto,
    )
    return {"resultado": "ok", "libro_id": evento.id, "tipo_evento": codigo_tipo}


# --------------------------------------------------------------------------- #
# Arribo OFFLINE (modo sin conexión de la app). ADITIVO: NO toca registrar_escaneo #
# ni el flujo online. Es un registro HISTÓRICO reenviado al recuperar señal.       #
# --------------------------------------------------------------------------- #
def registrar_arribo_offline(*, guardia_keycloak_id, qr_token, lat, lng, texto, ahora,
                             timestamp_evento=None, dispositivo_id=None):
    """Registra un arribo hecho SIN señal (tipo `arribo_sin_conexion`).

    Diferencias con el escaneo online (a propósito, por ser un registro histórico
    que la app reenvía al reconectar):
      - Tipo SIEMPRE `arribo_sin_conexion`: NO se valida geocerca/posición (nunca
        arribo_sin_geo/arribo_invalido). dentro_geocerca/distancia = NULL.
      - HORA: `timestamp_evento` (terreno, la hora real del escaneo) va a
        timestamp_evento; `ahora` (recepción) a timestamp_servidor. Si no vino
        timestamp válido -> se usa `ahora` en ambos (fallback, sin fallar).
      - NO exige ronda/ventana activa AHORA: la ronda se resuelve por la HORA del
        evento (timestamp_evento). Si ninguna calza, ronda_id queda NULL (igual se
        registra el arribo; es un dato válido).
      - COORDENADAS: si vienen (Decimal), se guardan tal cual; si faltan (None),
        se guardan 0 y 0 (no NULL) — así en los informes se ve el arribo sin señal.

    Devuelve el MISMO dict-discriminador que registrar_escaneo (lo traduce el mismo
    adaptador _evento_a_http): codigo_no_existe / catalogo_incompleto / ok (+ progreso).
    """
    ts_evento = timestamp_evento or ahora   # terreno (o servidor si no vino/ inválido)
    ts_servidor = ahora                      # recepción en el server

    cp = PuntoControl.objects.filter(qr_token=qr_token, activo=True).first()

    with transaction.atomic():
        if cp is None:
            # Mismo trato que online: se registra codigo_no_existe (con la hora de la
            # app) y el adaptador responde 404; así la app no reintenta para siempre.
            tipo_ne = TipoEvento.objects.filter(codigo="codigo_no_existe").first()
            if tipo_ne:
                LibroNovedades.objects.create(
                    instalacion_id=0,
                    guardia_keycloak_id=guardia_keycloak_id,
                    dispositivo_id=dispositivo_id,
                    tipo_evento=tipo_ne,
                    timestamp_evento=ts_evento,
                    timestamp_servidor=ts_servidor,
                    lat=lat,
                    lng=lng,
                    estado="error",
                    texto=texto or f"QR escaneado sin coincidencia (offline): {qr_token}",
                )
            return {"resultado": "codigo_no_existe"}

        tipo = TipoEvento.objects.filter(codigo="arribo_sin_conexion").first()
        if tipo is None:
            return {"resultado": "catalogo_incompleto"}

        # Sin coords -> 0/0 (no NULL): en informes queda claro "fue al punto sin señal".
        lat_val = lat if lat is not None else Decimal("0")
        lng_val = lng if lng is not None else Decimal("0")

        # Ronda del MOMENTO del evento (por su hora real), no de "ahora".
        ronda = _ronda_para_momento(cp.instalacion_id, ts_evento)

        evento = LibroNovedades.objects.create(
            instalacion_id=cp.instalacion_id,
            ronda_id=ronda.id if ronda else None,
            punto_control=cp,
            guardia_keycloak_id=guardia_keycloak_id,
            dispositivo_id=dispositivo_id,
            tipo_evento=tipo,
            timestamp_evento=ts_evento,
            timestamp_servidor=ts_servidor,
            lat=lat_val,
            lng=lng_val,
            distancia_metros=None,       # sin validación de posición
            dentro_geocerca=None,        # sin geocerca
            estado="ok",
            texto=texto,
        )

    resp = {
        "resultado": "ok",
        "libro_id": evento.id,
        "tipo_evento": tipo.codigo,       # arribo_sin_conexion
        "checkpoint": cp.nombre,
        "hora": timezone.localtime(ts_evento).strftime("%H:%M:%S"),
        "distancia_metros": None,
        "dentro_geocerca": None,
    }

    # Progreso del guardia en el TURNO del evento (cuenta puntos ÚNICOS). Se filtra
    # por timestamp_evento (hora real), no por timestamp_servidor (reenvío).
    if ronda is not None:
        ventana_turno = _ventana_turno(ronda, ts_evento)
        if ventana_turno is not None:
            estado = _estado_ejecucion(
                ronda.id, guardia_keycloak_id, ventana_turno, campo_tiempo="timestamp_evento"
            )
            resp["pertenece"] = cp.id in estado["punto_ids"]
            resp["progreso"] = {
                "escaneados": estado["escaneados"],
                "total": estado["total"],
                "puntos": estado["puntos"],
            }
            resp["completada"] = estado["total"] > 0 and estado["escaneados"] >= estado["total"]
    return resp
