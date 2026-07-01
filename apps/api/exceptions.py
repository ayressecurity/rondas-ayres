"""
Errores y respuestas JSON uniformes de la API.

TODO error de la API sale con el MISMO formato:

    {"error": {"codigo": "...", "mensaje": "..."}}

Nunca se filtran trazas ni detalles internos al cliente. El detalle técnico
(stack traces) queda en el log del servidor.
"""
import logging

from rest_framework.exceptions import (
    APIException,
    NotAuthenticated,
    NotFound,
    PermissionDenied,
)
from rest_framework.response import Response

log = logging.getLogger("apps.api")


def no_encontrado(mensaje, motivo="no_encontrado"):
    """NotFound (-> 404) con mensaje claro y motivo para el log estructurado.

    El handler usa el `mensaje` solo si la excepción trae `motivo` (así NO se
    filtran los textos por defecto en inglés de DRF)."""
    exc = NotFound(mensaje)
    exc.motivo = motivo
    return exc


def solicitud_invalida(mensaje, motivo="solicitud_invalida"):
    """APIException 400 con mensaje claro y motivo para el log."""
    exc = APIException(mensaje)
    exc.status_code = 400
    exc.motivo = motivo
    return exc


def sin_permiso(mensaje="No tienes permiso para ver este recurso.", motivo="sin_permiso"):
    """PermissionDenied (-> 403) con mensaje claro y motivo para el log.

    Para endpoints que exigen un rol concreto (p.ej. sspp/super_admin): el usuario
    está autenticado (token válido) pero NO autorizado. 403, no 401."""
    exc = PermissionDenied(mensaje)
    exc.motivo = motivo
    return exc


class DependenciaNoDisponible(APIException):
    """503: una dependencia externa (JWKS/Keycloak/catálogo) no está disponible."""
    status_code = 503
    default_detail = "Servicio de identidad no disponible. Intente más tarde."
    default_code = "dependencia_no_disponible"


def catalogo_no_disponible():
    """503 controlado: faltan los tipo_evento sembrados (seed_tipos_evento)."""
    exc = DependenciaNoDisponible(
        "El catálogo de eventos no está configurado. Contacte al administrador."
    )
    exc.motivo = "catalogo_incompleto"
    return exc


# Mapeo status HTTP -> código corto y mensaje genérico (fallback si la excepción
# no trae uno propio). Los mensajes son neutros, sin detalles internos.
_POR_STATUS = {
    400: ("solicitud_invalida", "Solicitud inválida."),
    401: ("no_autenticado", "No autenticado."),
    403: ("sin_permiso", "No tiene permiso para esta acción."),
    404: ("no_encontrado", "Recurso no encontrado."),
    405: ("metodo_no_permitido", "Método no permitido."),
    415: ("formato_no_soportado", "Formato no soportado."),
    429: ("demasiadas_solicitudes", "Demasiadas solicitudes."),
    500: ("error_interno", "Error interno del servidor."),
    503: ("dependencia_no_disponible", "Servicio no disponible. Intente más tarde."),
}


def _motivo_por_defecto(exc, status):
    """Motivo para el log estructurado cuando la excepción no trae uno."""
    motivo = getattr(exc, "motivo", None)
    if motivo:
        return motivo
    if isinstance(exc, NotAuthenticated):
        return "token_ausente"
    return _POR_STATUS.get(status, ("error", ""))[0]


def api_exception_handler(exc, context):
    """Exception handler de DRF: devuelve SIEMPRE {"error": {codigo, mensaje}}."""
    # Import diferido: evita un import circular al inicializar DRF (rest_framework
    # .views se carga mientras se resuelven los DEFAULT_AUTHENTICATION_CLASSES).
    from rest_framework.views import exception_handler as drf_exception_handler

    response = drf_exception_handler(exc, context)
    request = context.get("request")

    if response is None:
        # Excepción no controlada por DRF: 500. No exponemos nada del error.
        log.exception("error_no_controlado path=%s", getattr(request, "path", "?"))
        if request is not None:
            getattr(request, "_request", request)._api_motivo = "error_interno"
        return Response(
            {"error": {"codigo": "error_interno", "mensaje": "Error interno del servidor."}},
            status=500,
        )

    status = response.status_code
    # Código y mensaje uniformes por status HTTP (no exponemos los códigos
    # internos de DRF como "authentication_failed"/"not_authenticated").
    codigo, mensaje = _POR_STATUS.get(status, ("error", "Error."))

    # Para NUESTRAS fallas etiquetadas (token expirado/ausente/inválido) usamos
    # el mensaje claro que pusimos en la excepción; para el resto, el genérico.
    detalle = getattr(exc, "detail", None)
    if getattr(exc, "motivo", None) and isinstance(detalle, str):
        mensaje = str(detalle)

    # Guardamos el motivo en el HttpRequest subyacente para que el middleware
    # de log lo lea (context["request"] es el Request de DRF, que envuelve al real).
    if request is not None:
        getattr(request, "_request", request)._api_motivo = _motivo_por_defecto(exc, status)

    response.data = {"error": {"codigo": codigo, "mensaje": mensaje}}
    return response
