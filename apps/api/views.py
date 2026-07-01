"""
Vistas de la API móvil (DRF, stateless).

REGLA DE ORO: la identidad SIEMPRE sale del token (request.sub_con_guiones /
request.auth_claims), JAMÁS del body ni de la sesión. Todos los endpoints exigen
guardia autenticado (IsAuthenticated por defecto, ver REST_FRAMEWORK). NO hay
gate de super_admin: la API es para guardias.

Aislamiento por guardia: los listados "?mias" se filtran SIEMPRE por el
keycloak_id del token; nunca devuelven datos de otro guardia.
"""
import logging
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models import CharField, Prefetch, Q, Value
from django.db.models.functions import Cast, Lower, Replace
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    permission_classes,
    throttle_classes,
)
from rest_framework.exceptions import APIException
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from apps.api.exceptions import (
    catalogo_no_disponible,
    no_encontrado,
    sin_permiso,
    solicitud_invalida,
)
from apps.api.authentication import DeviceTokenAuthentication, KeycloakJWTAuthentication
from apps.api.throttling import EnrollThrottle
from apps.dispositivos.models import Dispositivo
from apps.dispositivos.utils import generar_token, hash_token
from apps.espejo.models import Cliente, Instalacion
from apps.api.serializers import (
    EventoCreateSerializer,
    NotificacionSerializer,
    PuntoControlByQrSerializer,
    RondaSerializer,
)
from apps.checkpoints.models import PuntoControl
from apps.comun.services.rondas import (
    SinRondaActiva,
    iniciar_o_reusar_ejecucion,
    registrar_escaneo,
    registrar_evento_simple,
    ventanas_de_alarma,
)
from apps.cuentas.identidad import norm_keycloak_id
from apps.novedades.models import LibroNovedades, LibroNovedadesMedia, TipoMedia
from apps.rondas.models import (
    DestinoNotificacion,
    EstadoGenerico,
    Notificacion,
    Programacion,
    ProgramacionHorario,
    Ronda,
    RondaGuardia,
    RondaSecuencia,
)

log = logging.getLogger("apps.api")

# Errores de zona horaria en el cambio de horario (DST). pytz puede no estar
# instalado (Django 5 usa zoneinfo, que no lanza en DST): import defensivo para
# no fallar y, si está, capturarlos y devolver 400 en vez de 500.
try:  # pragma: no cover
    from pytz.exceptions import AmbiguousTimeError, NonExistentTimeError
    _ERRORES_DST = (AmbiguousTimeError, NonExistentTimeError)
except ImportError:  # pragma: no cover
    _ERRORES_DST = ()


def _kc_norm(campo):
    """Expresión SQL que normaliza un keycloak_id (sin guiones, minúsculas).

    Misma normalización que apps.cuentas.identidad.norm_keycloak_id, pero a nivel
    de consulta, para casar columnas guardadas CON guiones contra el sub ya
    normalizado. Centralizado aquí para los tres endpoints."""
    texto = Cast(campo, output_field=CharField())
    return Lower(Replace(texto, Value("-"), Value(""), output_field=CharField()))


def _ids_rondas_del_guardia(sub):
    """IDs de las rondas asignadas al guardia (vía RondaGuardia).

    El guardia se identifica por el sub del TOKEN. ronda_guardia.guardia_keycloak_id
    va CON guiones; comparamos normalizando ambos lados (robusto ante variaciones).
    Devuelve un queryset de ids (se usa con __in)."""
    objetivo = norm_keycloak_id(sub)
    return (
        RondaGuardia.objects
        .annotate(kc_norm=_kc_norm("guardia_keycloak_id"))
        .filter(kc_norm=objetivo)
        .values_list("ronda_id", flat=True)
    )


@api_view(["GET"])
def me(request):
    """GET /api/me — eco de la identidad del token. Prueba del portero.

    Requiere token válido. Devuelve el sub (CON guiones), los roles del token,
    email, nombre y si la fila local se acaba de crear (alta JIT)."""
    claims = request.auth_claims
    nombre = f"{claims.get('given_name', '')} {claims.get('family_name', '')}".strip()
    return Response({
        "sub": request.sub_con_guiones,            # con guiones, como viene en el token
        "roles": request.token_roles,              # roles del realm (qué puede hacer)
        "email": claims.get("email"),
        "nombre": nombre or claims.get("preferred_username"),
        "creado_jit": request.creado_jit,          # True si la fila se creó en esta request
    })


@api_view(["GET"])
def checkpoint_by_qr(request, qr_token):
    """GET /api/checkpoints/by-qr/{qr_token} — resuelve el punto escaneado.

    Devuelve los datos del PuntoControl para que el móvil sepa qué punto leyó.
    QR inexistente o punto inactivo -> 404 (no distinguimos ambos casos al
    cliente: en los dos no hay punto utilizable)."""
    cp = PuntoControl.objects.filter(qr_token=qr_token, activo=True).first()
    if cp is None:
        raise no_encontrado("Punto de control no encontrado.", "checkpoint_no_encontrado")
    return Response(PuntoControlByQrSerializer(cp).data)


def _prefetch_rondas():
    """Prefetch para servir rondas SIN N+1: secuencia (con nombre del punto) y
    programación activa con sus horarios. Constante respecto al nº de rondas."""
    return [
        # select_related('punto_control') -> el nombre del punto sin query extra.
        Prefetch(
            "rondasecuencia_set",
            queryset=RondaSecuencia.objects.select_related("punto_control").order_by("orden"),
        ),
        # Solo programaciones ACTIVAS, con sus horarios ordenados por captura.
        Prefetch(
            "programacion_set",
            queryset=Programacion.objects.filter(activo=True).prefetch_related(
                Prefetch("programacionhorario_set", queryset=ProgramacionHorario.objects.order_by("orden"))
            ),
        ),
    ]


@api_view(["GET"])
@authentication_classes([DeviceTokenAuthentication, KeycloakJWTAuthentication])
def rondas_mias(request):
    """GET /api/rondas?mias — rondas para la app del guardia.

    Dos modos (el device_token manda):
      - CON X-Device-Token válido (app móvil): devuelve las rondas ACTIVAS de la
        INSTALACIÓN del dispositivo (la asignación por guardia no se usa). La
        instalación sale del dispositivo, nunca del body.
      - SIN device (clientes JWT actuales / Postman): comportamiento de SIEMPRE,
        las rondas asignadas al guardia vía RondaGuardia (fallback intacto).

    Cada ronda trae turno, programación (alarmas/vueltas con estado temporal) y la
    secuencia de puntos (con nombre) para armar la ruta."""
    ahora = timezone.now()  # zona Santiago (USE_TZ); el serializer etiqueta vueltas con esta hora
    contexto = {"ahora": ahora}

    dispositivo = getattr(request, "dispositivo", None)
    if dispositivo is not None:
        rondas = (
            Ronda.objects
            .filter(instalacion_id=dispositivo.instalacion_id, estado=EstadoGenerico.ACTIVA)
            .prefetch_related(*_prefetch_rondas())
            .order_by("nombre")
        )
        return Response(RondaSerializer(rondas, many=True, context=contexto).data)

    # Fallback sin device: SOLO las del guardia (vía RondaGuardia), como hoy.
    ids = _ids_rondas_del_guardia(request.sub_con_guiones)
    rondas = (
        Ronda.objects
        .filter(id__in=ids)
        .prefetch_related(*_prefetch_rondas())
        .order_by("nombre")
    )
    return Response(RondaSerializer(rondas, many=True, context=contexto).data)


@api_view(["GET"])
def notificaciones_mias(request):
    """GET /api/notificaciones?mias — recordatorios que aplican al guardia.

    Aplica si la notificación está activa y:
      - destino_tipo='guardia' y destino_ref == sub del token (la nombra a él), o
      - destino_tipo='todos' y el guardia está asignado a esa ronda.
    'grupo' NO se soporta aún (no hay modelo/convención de grupos): se omite.
    Aislamiento: 'guardia' solo casa con SU sub; 'todos' solo con SUS rondas."""
    objetivo = norm_keycloak_id(request.sub_con_guiones)
    mis_rondas = _ids_rondas_del_guardia(request.sub_con_guiones)
    notifs = (
        Notificacion.objects
        .filter(estado=EstadoGenerico.ACTIVA)
        .annotate(ref_norm=_kc_norm("destino_ref"))
        .filter(
            Q(destino_tipo=DestinoNotificacion.GUARDIA, ref_norm=objetivo)
            | Q(destino_tipo=DestinoNotificacion.TODOS, ronda_id__in=mis_rondas)
        )
        .order_by("-id")
    )
    return Response(NotificacionSerializer(notifs, many=True).data)


def _primer_error(errores):
    """Primer mensaje de error legible del serializer (para un 400 claro)."""
    for _campo, detalle in errores.items():
        if isinstance(detalle, (list, tuple)) and detalle:
            return str(detalle[0])
        return str(detalle)
    return "Solicitud inválida."


def _evento_a_http(res):
    """Traduce el `resultado` del service a la respuesta HTTP de la API.

    Mapeo (mismos resultados que produce registrar_escaneo):
      - ok                     -> 201 (evento nuevo registrado)
      - ya_escaneado           -> 200 (caso normal: idempotencia por turno)
      - codigo_no_existe       -> 404 (igual que la web; el evento ya quedó registrado)
      - punto_otra_instalacion -> 400 (robustez; no debería ocurrir por opción a)
      - catalogo_incompleto    -> 503 controlado (faltan tipo_evento sembrados)
      - cualquier otro         -> 500 controlado (sin trazas)
    """
    resultado = res["resultado"]
    if resultado == "ok":
        salida = {
            "id": res["libro_id"],            # id del libro_novedades creado
            "tipo_evento": res["tipo_evento"],  # arribo / arribo_invalido / arribo_sin_geo
            "dentro_geocerca": res["dentro_geocerca"],
            "distancia_metros": res["distancia_metros"],
        }
        if "progreso" in res:                 # solo si hay ronda/ejecución en curso
            salida["progreso"] = res["progreso"]
        return Response(salida, status=status.HTTP_201_CREATED)

    if resultado == "ya_escaneado":
        # Idempotencia de negocio: NO es error, es un caso normal.
        salida = {"ya_registrado": True, "mensaje": "Ya registraste este punto en esta ronda."}
        if "progreso" in res:
            salida["progreso"] = res["progreso"]
        return Response(salida, status=status.HTTP_200_OK)

    if resultado == "codigo_no_existe":
        # Igual que la web: el evento codigo_no_existe ya quedó registrado por el
        # service; se avisa con 404 (la vista web también responde 404 aquí).
        raise no_encontrado("El código QR no corresponde a ningún punto de control.", "codigo_no_existe")

    if resultado == "punto_otra_instalacion":
        # No ocurre por la opción a (instalación derivada del propio punto), pero
        # se mapea por robustez.
        raise solicitud_invalida("Este QR no pertenece a esta instalación.", "punto_otra_instalacion")

    if resultado == "sin_ronda_activa":
        # No hay ronda activa en este horario: no se registró nada (decisión #8).
        raise solicitud_invalida(
            "Este QR no pertenece a una ronda activa en este horario.", "sin_ronda_activa"
        )

    if resultado == "sin_ventana_activa":
        # Fuera de toda ventana de alarma (antes de la 1ª alarma o en un hueco).
        raise solicitud_invalida(
            "No tienes una ronda programada en este horario.", "sin_ventana_activa"
        )

    if resultado == "catalogo_incompleto":
        raise catalogo_no_disponible()

    # Resultado inesperado: error controlado, sin filtrar nada.
    log.error("resultado_inesperado resultado=%s", resultado)
    exc = APIException("No se pudo registrar el evento.")
    exc.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    exc.motivo = "resultado_inesperado"
    raise exc


@api_view(["POST"])
@authentication_classes([DeviceTokenAuthentication, KeycloakJWTAuthentication])
def crear_evento(request):
    """POST /api/eventos — registra una marca del guardia desde la app móvil.

    Adaptador DELGADO: valida el body, deriva la instalación del QR, y llama al
    MISMO service que el escáner web (iniciar_o_reusar_ejecucion + registrar_escaneo).
    Toda la lógica de negocio vive en el service; aquí solo se parsea y se traduce.

    Dos identidades (Fase 4): el GUARDIA sale del token (sub) y el DISPOSITIVO del
    header X-Device-Token (opcional). Si viene un dispositivo válido se aplica el
    DOBLE CANDADO de instalación y se sella libro_novedades.dispositivo_id.
    """
    # 1) Validar el body. La identidad y la instalación NO salen de aquí.
    ser = EventoCreateSerializer(data=request.data)
    if not ser.is_valid():
        raise solicitud_invalida(_primer_error(ser.errors), "body_invalido")
    datos = ser.validated_data

    # 2) Identidad SIEMPRE del token (sub CON guiones que dejó el portero del A).
    #    Aunque el body trajera keycloak_id/guardia/sub, el serializer los descartó.
    guardia = request.sub_con_guiones

    qr_token = datos["qr_token"].strip()
    # float, igual que la vista web (_parse_coord): mismo cálculo y mismo guardado.
    lat = float(datos["lat"])
    lng = float(datos["lng"])
    texto = (datos.get("texto") or "").strip() or None

    # 3) Hora de TERRENO (offline) si vino; se hace aware en Santiago. La hora del
    #    SERVIDOR (ahora) se calcula aparte y es la que irá a timestamp_servidor.
    ts_terreno = datos.get("timestamp_evento")
    if ts_terreno is not None and timezone.is_naive(ts_terreno):
        try:
            ts_terreno = timezone.make_aware(ts_terreno)
        except (*_ERRORES_DST, ValueError, OverflowError):
            # Hora ambigua/inexistente por el cambio de horario (DST) u otra. No 500.
            raise solicitud_invalida(
                "La hora del evento (timestamp_evento) no es válida.", "timestamp_invalido"
            )
    ahora = timezone.now()  # hora REAL del servidor (Santiago vía timezone)

    # 4) Instalación DERIVADA del propio punto del QR (opción a, stateless). El
    #    service vuelve a resolver el punto; aquí lo resolvemos para derivar la
    #    instalación y para iniciar/reusar la ejecución de la ronda de la hora.
    dispositivo = getattr(request, "dispositivo", None)  # del X-Device-Token (o None)

    punto = PuntoControl.objects.filter(qr_token=qr_token, activo=True).first()
    if punto is not None:
        # DOBLE CANDADO (en la vista, no en el service): si vino un dispositivo,
        # debe estar enrolado en la MISMA instalación del punto escaneado. Es un
        # candado NUEVO y distinto de "punto_otra_instalacion" (ese compara el
        # contexto del llamador vs el punto; este compara el dispositivo vs el punto).
        if dispositivo is not None and dispositivo.instalacion_id != punto.instalacion_id:
            raise solicitud_invalida(
                "Este teléfono no está enrolado en la instalación de este punto.",
                "dispositivo_otra_instalacion",
            )
        instalacion_id = punto.instalacion_id
        try:
            # Si hay ronda activa, asegura/retoma la ejecución (habilita el bloqueo
            # de re-escaneo). Si NO hay, el service rechaza con "sin_ronda_activa".
            iniciar_o_reusar_ejecucion(
                instalacion_id=instalacion_id,
                guardia_keycloak_id=guardia,
                ahora=ahora,
            )
        except SinRondaActiva:
            pass  # el service vuelve a comprobarlo y devuelve "sin_ronda_activa"
    else:
        instalacion_id = None  # codigo_no_existe: el service usa instalacion_id=0

    # 5) Registrar vía el service compartido y traducir el resultado a HTTP.
    res = registrar_escaneo(
        instalacion_id=instalacion_id,
        guardia_keycloak_id=guardia,
        qr_token=qr_token,
        lat=lat,
        lng=lng,
        texto=texto,
        ahora=ahora,
        timestamp_evento=ts_terreno,  # terreno (offline) o None -> el service usa ahora
        dispositivo_id=dispositivo.id if dispositivo is not None else None,
    )
    return _evento_a_http(res)


# Extensiones permitidas -> tipo de media (libro_novedades_media.tipo).
# Se valida por EXTENSIÓN (el content-type del cliente no es de fiar): generamos
# nuestro propio nombre y conservamos solo esta extensión validada.
_EXT_A_TIPO = {
    "jpg": TipoMedia.FOTO, "jpeg": TipoMedia.FOTO, "png": TipoMedia.FOTO, "webp": TipoMedia.FOTO,
    "mp3": TipoMedia.AUDIO, "m4a": TipoMedia.AUDIO, "ogg": TipoMedia.AUDIO,
    "mp4": TipoMedia.VIDEO,
}
_TIPOS_ACEPTADOS_TXT = "foto (jpg, jpeg, png, webp), audio (mp3, m4a, ogg), video (mp4)"


def _limite_mb(tipo):
    """MB máximos para ese tipo, leídos de settings (configurables por .env)."""
    return {
        TipoMedia.FOTO: settings.MEDIA_MAX_FOTO_MB,
        TipoMedia.AUDIO: settings.MEDIA_MAX_AUDIO_MB,
        TipoMedia.VIDEO: settings.MEDIA_MAX_VIDEO_MB,
    }[tipo]


def _extension(nombre):
    """Extensión en minúsculas del nombre subido (sin el punto), o '' si no tiene."""
    nombre = nombre or ""
    return nombre.rsplit(".", 1)[-1].lower() if "." in nombre else ""


@api_view(["POST"])
def subir_media(request, evento_id):
    """POST /api/eventos/{id}/media — adjunta archivos (foto/audio/video) a un evento.

    Adaptador delgado: verifica propiedad por el TOKEN, valida TODOS los archivos
    (tipo + tamaño) ANTES de guardar (todo-o-nada) y crea las filas en
    libro_novedades_media con el archivo físico en MEDIA.
    """
    sub = request.sub_con_guiones  # identidad del TOKEN, CON guiones

    # PROPIEDAD: el evento debe ser del guardia del token. Si no existe O es de
    # OTRO guardia -> 404 (mismo error en ambos casos: NO revelamos la existencia
    # de eventos ajenos). Comparación normalizada (contrato de identidad).
    evento = LibroNovedades.objects.filter(id=evento_id).first()
    if evento is None or norm_keycloak_id(evento.guardia_keycloak_id) != norm_keycloak_id(sub):
        raise no_encontrado("Evento no encontrado.", "evento_no_encontrado")

    archivos = request.FILES.getlist("archivo")
    if not archivos:
        raise solicitud_invalida("No se recibió ningún archivo (campo 'archivo').", "sin_archivo")

    creados = guardar_media(evento, archivos)
    log.info("media_subida evento=%s sub=%s archivos=%s", evento.id, sub, len(creados))
    return Response(creados, status=status.HTTP_201_CREATED)


def guardar_media(evento, archivos):
    """Valida (tipo + tamaño) y guarda TODOS los archivos en libro_novedades_media
    del evento dado. Todo-o-nada: valida antes de escribir nada; si uno falla,
    NO se persiste ninguno. Devuelve [{"id","tipo","url_relativa"}, ...].

    Extraído de subir_media (comportamiento observable idéntico) para reutilizarlo
    también en el inicio de sesión. Lanza `solicitud_invalida` con los MISMOS
    códigos (tipo_no_permitido / tamano_excedido)."""
    # 1) VALIDAR TODOS antes de guardar nada (para no dejar media a medias).
    validados = []  # [(archivo, tipo, ext), ...]
    for f in archivos:
        ext = _extension(f.name)
        tipo = _EXT_A_TIPO.get(ext)
        if tipo is None:
            raise solicitud_invalida(
                f"Tipo de archivo no permitido: «{f.name}». Aceptados: {_TIPOS_ACEPTADOS_TXT}.",
                "tipo_no_permitido",
            )
        limite_bytes = _limite_mb(tipo) * 1024 * 1024
        if f.size > limite_bytes:
            raise solicitud_invalida(
                f"El archivo «{f.name}» excede el máximo de {_limite_mb(tipo)} MB para {tipo}.",
                "tamano_excedido",
            )
        validados.append((f, tipo, ext))

    # 2) GUARDAR todos (archivo físico + fila) en una transacción.
    creados = []
    with transaction.atomic():
        for f, tipo, ext in validados:
            # Nombre PROPIO (uuid) -> evita path traversal y colisiones; solo se
            # conserva la extensión validada. Carpeta por evento.
            destino = f"libro_novedades/{evento.id}/{uuid4().hex}.{ext}"
            path = default_storage.save(destino, ContentFile(f.read()))
            media = LibroNovedadesMedia.objects.create(
                libro_novedades=evento, tipo=tipo, path=path,
            )
            creados.append({"id": media.id, "tipo": media.tipo, "url_relativa": default_storage.url(path)})
    return creados


# ---------------------------------------------------------------------------
# Enrolamiento de dispositivos (Fase 3) — endpoint PÚBLICO.
#
# El teléfono aún no tiene credencial, así que aquí NO corre el portero JWT:
# overrides POR-VISTA (authentication_classes([]) + AllowAny). El resto de la API
# sigue exigiendo Bearer. Protegido con EnrollThrottle (1 intento/30 min por IP).
# La instalación se deriva SIEMPRE del secreto en el servidor, jamás del body.
# ---------------------------------------------------------------------------
@api_view(["POST"])
@authentication_classes([])          # sin JWT: enrolamiento público
@permission_classes([AllowAny])
@throttle_classes([EnrollThrottle])  # anti fuerza bruta del secreto
def enroll_dispositivo(request):
    """POST /api/dispositivos/enroll — enrola un teléfono a una instalación.

    Body: {"s": "<secreto del QR>", "nombre": "<opcional>", "device_info": {opcional}}.

    - La instalación se resuelve por instalacion.qr == s (SIEMPRE en el servidor;
      el body NO puede elegir instalación).
    - Secreto ausente o inválido -> 400 genérico. NO revelamos si el secreto
      existe ni lo logueamos.
    - OK -> crea un Dispositivo nuevo (re-enrolar siempre crea fila nueva) y
      devuelve el token EN PLANO una sola vez (show-once): en BD solo queda su
      hash SHA-256, no hay forma de recuperarlo después.
    """
    secreto = (request.data.get("s") or "").strip()
    instalacion = Instalacion.objects.filter(qr=secreto).first() if secreto else None
    if instalacion is None:
        # Mismo error para "ausente" e "inexistente": no filtramos la existencia.
        raise solicitud_invalida("Código de configuración inválido.", "enrolamiento_invalido")

    # Token individual del dispositivo. Se entrega UNA vez; en BD solo el hash.
    token = generar_token()
    nombre = (request.data.get("nombre") or "").strip()[:120]
    device_info = request.data.get("device_info")
    if not isinstance(device_info, (dict, list)):
        device_info = None  # solo aceptamos JSON estructurado; lo demás se ignora

    dispositivo = Dispositivo.objects.create(
        instalacion_id=instalacion.id,     # SIEMPRE del secreto, NUNCA del body
        token_hash=hash_token(token),
        nombre=nombre,
        device_info=device_info,
    )
    log.info("dispositivo_enrolado id=%s instalacion=%s", dispositivo.id, instalacion.id)

    return Response(
        {
            "device_token": token,         # show-once: NO se vuelve a poder recuperar
            "dispositivo_id": dispositivo.id,
            "instalacion": {"id": instalacion.id, "nombre": instalacion.nombre},
        },
        status=status.HTTP_201_CREATED,
    )


# ---------------------------------------------------------------------------
# Sesión de turno: inicio (la app lo llama al loguearse el guardia).
# Registra sesion_inicio en libro_novedades. Identidad del guardia del token;
# instalación del dispositivo (X-Device-Token obligatorio). Fotos OPCIONALES.
# ---------------------------------------------------------------------------
_MAX_FOTOS_SESION = 2


def _coord_opcional(valor):
    """lat/lng OPCIONAL del form-data: Decimal (precisión completa) si es numérico;
    None si falta o no es numérico. NO rechaza: el GPS es opcional, un valor
    vacío/malformado se trata como ausente para no bloquear al guardia."""
    if valor is None or str(valor).strip() == "":
        return None
    try:
        return Decimal(str(valor).strip())
    except (InvalidOperation, ValueError, TypeError):
        return None


@api_view(["POST"])
@authentication_classes([DeviceTokenAuthentication, KeycloakJWTAuthentication])
def sesion_inicio(request):
    """POST /api/sesion/inicio — registra el inicio de turno del guardia.

    multipart/form-data. Campos: `fotos` (0..2, OPCIONALES), `texto` (opcional).
    Sin GPS. El guardia sale del token; la instalación, del dispositivo.

    - Sin X-Device-Token (o dispositivo inválido) -> 400 dispositivo_requerido.
    - >2 fotos -> 400. Foto inválida (tipo/tamaño) -> 400 SIN registrar nada
      (todo-o-nada: el evento y sus medios viven en una sola transacción).
    - OK -> 201 con id, tipo_evento, instalacion_id, hora (HH:MM:SS) y fotos[].
    """
    dispositivo = getattr(request, "dispositivo", None)
    if dispositivo is None:
        raise solicitud_invalida("Falta el dispositivo (X-Device-Token).", "dispositivo_requerido")

    fotos = request.FILES.getlist("fotos")
    if len(fotos) > _MAX_FOTOS_SESION:
        raise solicitud_invalida(
            f"Máximo {_MAX_FOTOS_SESION} imágenes en el inicio de sesión.", "demasiadas_fotos"
        )

    texto = (request.data.get("texto") or "").strip() or None
    # GPS OPCIONAL: si no viene o es inválido, quedan null (no bloquea el inicio).
    lat = _coord_opcional(request.data.get("lat"))
    lng = _coord_opcional(request.data.get("lng"))
    ahora = timezone.now()  # hora REAL del servidor (Santiago vía timezone)

    # Todo-o-nada: si una foto es inválida, guardar_media lanza y la transacción
    # revierte también el evento recién creado (no queda un sesion_inicio suelto).
    with transaction.atomic():
        res = registrar_evento_simple(
            instalacion_id=dispositivo.instalacion_id,   # del dispositivo, NUNCA del body
            guardia_keycloak_id=request.sub_con_guiones,  # del token, CON guiones
            codigo_tipo="sesion_inicio",
            dispositivo_id=dispositivo.id,
            texto=texto,
            lat=lat,
            lng=lng,
            ahora=ahora,
        )
        if res["resultado"] == "catalogo_incompleto":
            raise catalogo_no_disponible()

        evento = LibroNovedades.objects.get(id=res["libro_id"])
        medios = guardar_media(evento, fotos) if fotos else []

    log.info("sesion_inicio evento=%s dispositivo=%s fotos=%s", res["libro_id"], dispositivo.id, len(medios))
    return Response(
        {
            "id": res["libro_id"],
            "tipo_evento": "sesion_inicio",
            "instalacion_id": dispositivo.instalacion_id,
            "hora": timezone.localtime(ahora).strftime("%H:%M:%S"),
            "lat": lat,
            "lng": lng,
            "fotos": [{"id": m["id"], "url_relativa": m["url_relativa"]} for m in medios],
        },
        status=status.HTTP_201_CREATED,
    )


# ---------------------------------------------------------------------------
# Novedad desde el móvil: como "Reportar novedad" de la web, pero por la app.
# Sin QR/punto/ronda: solo observación (texto OBLIGATORIO) + fotos OPCIONALES.
# Guardia del token; instalación del dispositivo (X-Device-Token obligatorio).
# ---------------------------------------------------------------------------
@api_view(["POST"])
@authentication_classes([DeviceTokenAuthentication, KeycloakJWTAuthentication])
def crear_novedad(request):
    """POST /api/novedades — registra una novedad (tipo_evento 'novedad').

    multipart/form-data. Campos: `texto` (OBLIGATORIO), `fotos` (0..2, OPCIONALES).

    - Sin X-Device-Token (o dispositivo inválido) -> 400 dispositivo_requerido.
    - `texto` vacío/ausente -> 400 (la observación es obligatoria).
    - >2 fotos o foto inválida (tipo/tamaño) -> 400 SIN registrar nada
      (todo-o-nada: evento + medios en una sola transacción).
    - OK -> 201 con id, tipo_evento, instalacion_id, hora (HH:MM:SS) y fotos[].
    """
    dispositivo = getattr(request, "dispositivo", None)
    if dispositivo is None:
        raise solicitud_invalida("Falta el dispositivo (X-Device-Token).", "dispositivo_requerido")

    texto = (request.data.get("texto") or "").strip()
    if not texto:
        raise solicitud_invalida("La observación es obligatoria.", "texto_requerido")

    fotos = request.FILES.getlist("fotos")
    if len(fotos) > _MAX_FOTOS_SESION:
        raise solicitud_invalida(
            f"Máximo {_MAX_FOTOS_SESION} imágenes por novedad.", "demasiadas_fotos"
        )

    ahora = timezone.now()  # hora REAL del servidor (Santiago vía timezone)

    # Todo-o-nada: si una foto es inválida, guardar_media lanza y la transacción
    # revierte también el evento recién creado.
    with transaction.atomic():
        res = registrar_evento_simple(
            instalacion_id=dispositivo.instalacion_id,   # del dispositivo, NUNCA del body
            guardia_keycloak_id=request.sub_con_guiones,  # del token, CON guiones
            codigo_tipo="novedad",
            dispositivo_id=dispositivo.id,
            texto=texto,
            ahora=ahora,
        )
        if res["resultado"] == "catalogo_incompleto":
            raise catalogo_no_disponible()

        evento = LibroNovedades.objects.get(id=res["libro_id"])
        medios = guardar_media(evento, fotos) if fotos else []

    log.info("novedad_movil evento=%s dispositivo=%s fotos=%s", res["libro_id"], dispositivo.id, len(medios))
    return Response(
        {
            "id": res["libro_id"],
            "tipo_evento": "novedad",
            "instalacion_id": dispositivo.instalacion_id,
            "hora": timezone.localtime(ahora).strftime("%H:%M:%S"),
            "fotos": [{"id": m["id"], "url_relativa": m["url_relativa"]} for m in medios],
        },
        status=status.HTTP_201_CREATED,
    )


# ---------------------------------------------------------------------------
# Cancelar ronda: el guardia cancela desde la app una ronda que no puede hacer,
# con el motivo. Registra ronda_cancelada. Sin foto, sin GPS. Guardia del token;
# instalación del dispositivo; la ronda debe pertenecer a esa instalación.
# ---------------------------------------------------------------------------
@api_view(["POST"])
@authentication_classes([DeviceTokenAuthentication, KeycloakJWTAuthentication])
def cancelar_ronda(request, ronda_id):
    """POST /api/rondas/<ronda_id>/cancelar — registra la cancelación de una ronda.

    Campos: `texto` (observación OBLIGATORIA). Sin foto, sin GPS.

    - Sin X-Device-Token (o dispositivo inválido) -> 400 dispositivo_requerido.
    - Observación vacía/ausente -> 400.
    - La ronda debe existir y ser de la instalación del dispositivo; si no ->
      404 (mismo aislamiento que el resto: no se cancelan rondas ajenas).
    - OK -> 201 con id, tipo_evento, instalacion_id, ronda_id, hora, observacion.
    """
    dispositivo = getattr(request, "dispositivo", None)
    if dispositivo is None:
        raise solicitud_invalida("Falta el dispositivo (X-Device-Token).", "dispositivo_requerido")

    observacion = (request.data.get("texto") or "").strip()
    if not observacion:
        raise solicitud_invalida(
            "La observación es obligatoria para cancelar una ronda.", "observacion_requerida"
        )

    # Aislamiento: la ronda debe existir Y ser de la instalación del dispositivo.
    ronda = Ronda.objects.filter(id=ronda_id, instalacion_id=dispositivo.instalacion_id).first()
    if ronda is None:
        raise no_encontrado("Ronda no encontrada.", "ronda_no_encontrada")

    ahora = timezone.now()  # hora REAL del servidor (Santiago vía timezone)
    res = registrar_evento_simple(
        instalacion_id=dispositivo.instalacion_id,   # del dispositivo, NUNCA del body
        guardia_keycloak_id=request.sub_con_guiones,  # del token, CON guiones
        codigo_tipo="ronda_cancelada",
        dispositivo_id=dispositivo.id,
        texto=observacion,
        ahora=ahora,
        ronda_id=ronda.id,                            # deja constancia de QUÉ ronda
    )
    if res["resultado"] == "catalogo_incompleto":
        raise catalogo_no_disponible()

    log.info("ronda_cancelada evento=%s ronda=%s dispositivo=%s", res["libro_id"], ronda.id, dispositivo.id)
    return Response(
        {
            "id": res["libro_id"],
            "tipo_evento": "ronda_cancelada",
            "instalacion_id": dispositivo.instalacion_id,
            "ronda_id": ronda.id,
            "hora": timezone.localtime(ahora).strftime("%H:%M:%S"),
            "observacion": observacion,
        },
        status=status.HTTP_201_CREATED,
    )


# ---------------------------------------------------------------------------
# Catálogo de instalaciones para la app móvil: TODAS las vigentes (de todas las
# instalaciones), con el nombre del cliente resuelto desde el espejo. Solo token
# de guardia (Bearer); NO necesita X-Device-Token.
# ---------------------------------------------------------------------------
@api_view(["GET"])
@authentication_classes([KeycloakJWTAuthentication])
def listar_instalaciones(request):
    """GET /api/instalaciones — instalaciones vigentes con su cliente.

    Vigentes = no eliminadas (deleted_at IS NULL), mismo criterio que el
    repositorio del espejo (apps/espejo/repositorio.py). Orden por nombre asc.

    Cada item: {"id","nombre","cliente":{"id","nombre"}}; cliente = null si el
    cliente_id no resuelve en la tabla cliente. El nombre del cliente se resuelve
    con UN mapa batch (una sola query), sin N+1.
    """
    instalaciones = list(
        Instalacion.objects
        .filter(deleted_at__isnull=True)
        .order_by("nombre")
        .values("id", "nombre", "cliente_id")
    )
    # Mapa cliente_id -> razon_social en 1 query (sin consultar por instalación).
    cliente_ids = {i["cliente_id"] for i in instalaciones}
    clientes = dict(Cliente.objects.filter(id__in=cliente_ids).values_list("id", "razon_social"))

    data = [
        {
            "id": i["id"],
            "nombre": i["nombre"],
            "cliente": (
                {"id": i["cliente_id"], "nombre": clientes[i["cliente_id"]]}
                if i["cliente_id"] in clientes else None
            ),
        }
        for i in instalaciones
    ]
    return Response(data)


# ---------------------------------------------------------------------------
# Resumen de una instalación para la vista SSPP de la app: cliente + rondas (con
# turno y horas de alarma) + total de checkpoints. Solo rol sspp / super_admin.
# ---------------------------------------------------------------------------
def _prefetch_programacion():
    """Prefetch de la programación ACTIVA + sus horarios ordenados.

    Es la MISMA parte de programación que _prefetch_rondas, pero SIN la secuencia
    (el resumen no la usa): constante respecto al nº de rondas (sin N+1)."""
    return Prefetch(
        "programacion_set",
        queryset=Programacion.objects.filter(activo=True).prefetch_related(
            Prefetch("programacionhorario_set", queryset=ProgramacionHorario.objects.order_by("orden"))
        ),
    )


def _resumen_ronda(ronda, ahora):
    """Resumen simple de una ronda: turno (HH:MM), cruce de medianoche y las horas
    de alarma de la programación activa.

    REUTILIZA `ventanas_de_alarma` (la MISMA lógica que GET /api/rondas?mias) sobre
    los horarios YA prefetcheados, para no reconsultar ni reinventar el anclaje de
    alarmas / cruce de medianoche. Aquí NO se calcula estado temporal ni
    vuelta_actual: es solo informativo (horas + total de vueltas)."""
    # Aplana los ProgramacionHorario de las programaciones ACTIVAS ya prefetcheadas
    # (igual que RondaSerializer._horarios_precargados; sin tocar la BD).
    horarios = [
        h
        for prog in ronda.programacion_set.all()        # prefetch: solo activo=True
        for h in prog.programacionhorario_set.all()     # prefetch: order_by('orden')
    ]
    ventanas = ventanas_de_alarma(ronda, ahora, horarios=horarios)
    # Cada ventana la abre una alarma (ProgramacionHorario); su hora en HH:MM,
    # ordenadas por la propia ventana (línea de tiempo del turno).
    horas = [f"{h.hora:02d}:{h.minuto:02d}" for (h, _ini, _fin) in ventanas]

    hi, hf = ronda.hora_inicio, ronda.hora_fin
    return {
        "id": ronda.id,
        "nombre": ronda.nombre,
        "hora_inicio": hi.strftime("%H:%M") if hi else None,
        "hora_fin": hf.strftime("%H:%M") if hf else None,
        "cruza_medianoche": bool(hi and hf and hi > hf),  # el turno cruza medianoche
        "total_vueltas": len(ventanas),                   # nº de alarmas activas (0 si no tiene)
        "horarios": horas,
    }


@api_view(["GET"])
@authentication_classes([KeycloakJWTAuthentication])
def resumen_instalacion(request, instalacion_id):
    """GET /api/instalaciones/<id>/resumen — resumen para la vista SSPP de la app.

    Solo rol sspp / super_admin (roles del token, realm_access.roles). El resto:
    403. Sin Bearer: 401. Instalación inexistente o eliminada (deleted_at): 404.

    Devuelve el cliente (razón social del espejo), el total de checkpoints ACTIVOS
    y las rondas ACTIVAS de la instalación, cada una con su turno (HH:MM), si cruza
    medianoche, el total de vueltas y las horas de alarma de la programación activa.
    Reutiliza ventanas_de_alarma (como GET /api/rondas?mias); sin estado temporal.
    """
    # PERMISO: la API es de guardias por defecto; este endpoint es de monitoreo,
    # así que exige rol sspp o super_admin. Los roles salen del TOKEN (los dejó el
    # portero en request.token_roles = realm_access.roles), nunca del body.
    roles = getattr(request, "token_roles", []) or []
    if "super_admin" not in roles and "sspp" not in roles:
        raise sin_permiso()

    # Instalación VIGENTE (no eliminada). Mismo criterio que el resto (deleted_at).
    inst = (
        Instalacion.objects
        .filter(id=instalacion_id, deleted_at__isnull=True)
        .values("id", "nombre", "cliente_id")
        .first()
    )
    if inst is None:
        raise no_encontrado("Instalación no encontrada.", "instalacion_no_encontrada")

    # Cliente (razón social) por lookup directo del cliente_id: 1 query, sin N+1.
    razon = (
        Cliente.objects
        .filter(id=inst["cliente_id"])
        .values_list("razon_social", flat=True)
        .first()
    )
    cliente = {"id": inst["cliente_id"], "nombre": razon} if razon is not None else None

    # Checkpoints ACTIVOS de la instalación (conteo, no listado).
    checkpoints_total = PuntoControl.objects.filter(
        instalacion_id=inst["id"], activo=True
    ).count()

    # Rondas ACTIVAS + su programación activa (prefetch: sin N+1 por ronda).
    ahora = timezone.now()  # zona Santiago (USE_TZ); ancla el turno de las alarmas
    rondas = (
        Ronda.objects
        .filter(instalacion_id=inst["id"], estado=EstadoGenerico.ACTIVA)
        .prefetch_related(_prefetch_programacion())
        .order_by("nombre")
    )
    rondas_data = [_resumen_ronda(r, ahora) for r in rondas]

    return Response({
        "instalacion": {"id": inst["id"], "nombre": inst["nombre"]},
        "cliente": cliente,
        "checkpoints_total": checkpoints_total,
        "rondas": rondas_data,
    })
