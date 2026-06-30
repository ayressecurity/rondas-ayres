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
    solicitud_invalida,
)
from apps.api.throttling import EnrollThrottle
from apps.dispositivos.models import Dispositivo
from apps.dispositivos.utils import generar_token, hash_token
from apps.espejo.models import Instalacion
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
)
from apps.cuentas.identidad import norm_keycloak_id
from apps.novedades.models import LibroNovedades, LibroNovedadesMedia, TipoMedia
from apps.rondas.models import (
    DestinoNotificacion,
    EstadoGenerico,
    Notificacion,
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


@api_view(["GET"])
def rondas_mias(request):
    """GET /api/rondas?mias — rondas asignadas al guardia del token.

    SOLO las del guardia (vía RondaGuardia); jamás las de otro. Cada ronda trae
    su secuencia de puntos (punto_control_id + orden) para armar la ruta."""
    ids = _ids_rondas_del_guardia(request.sub_con_guiones)
    rondas = (
        Ronda.objects
        .filter(id__in=ids)
        # La secuencia se trae ordenada por 'orden' en un solo golpe (sin N+1).
        .prefetch_related(
            Prefetch("rondasecuencia_set", queryset=RondaSecuencia.objects.order_by("orden"))
        )
        .order_by("nombre")
    )
    return Response(RondaSerializer(rondas, many=True).data)


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
def crear_evento(request):
    """POST /api/eventos — registra una marca del guardia desde la app móvil.

    Adaptador DELGADO: valida el body, deriva la instalación del QR, y llama al
    MISMO service que el escáner web (iniciar_o_reusar_ejecucion + registrar_escaneo).
    Toda la lógica de negocio vive en el service; aquí solo se parsea y se traduce.
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
    punto = PuntoControl.objects.filter(qr_token=qr_token, activo=True).first()
    if punto is not None:
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

    # 1) VALIDAR TODOS antes de guardar nada (todo-o-nada: si uno falla, no se
    #    persiste ninguno, para no dejar media a medias).
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

    log.info("media_subida evento=%s sub=%s archivos=%s", evento.id, sub, len(creados))
    return Response(creados, status=status.HTTP_201_CREATED)


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
