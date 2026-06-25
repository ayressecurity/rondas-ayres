"""
Vistas de la API móvil (DRF, stateless).

REGLA DE ORO: la identidad SIEMPRE sale del token (request.sub_con_guiones /
request.auth_claims), JAMÁS del body ni de la sesión. Todos los endpoints exigen
guardia autenticado (IsAuthenticated por defecto, ver REST_FRAMEWORK). NO hay
gate de super_admin: la API es para guardias.

Aislamiento por guardia: los listados "?mias" se filtran SIEMPRE por el
keycloak_id del token; nunca devuelven datos de otro guardia.
"""
from django.db.models import CharField, Prefetch, Q, Value
from django.db.models.functions import Cast, Lower, Replace
from rest_framework.decorators import api_view
from rest_framework.response import Response

from apps.api.exceptions import no_encontrado
from apps.api.serializers import (
    NotificacionSerializer,
    PuntoControlByQrSerializer,
    RondaSerializer,
)
from apps.checkpoints.models import PuntoControl
from apps.cuentas.identidad import norm_keycloak_id
from apps.rondas.models import (
    DestinoNotificacion,
    EstadoGenerico,
    Notificacion,
    Ronda,
    RondaGuardia,
    RondaSecuencia,
)


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
