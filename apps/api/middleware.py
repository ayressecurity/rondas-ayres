"""
Log estructurado de cada request a la API.

Una línea por request a /api/, en formato "clave=valor" (fácil de grepear):

    api method=GET path=/api/me status=200 sub=<uuid> motivo=-

- sub: el 'sub' del token si se autenticó; "anon" si no.
- motivo: la causa del rechazo (token_expirado, jwks_no_disponible, ...) o "-".

NUNCA se loguea el token. El portero (authentication.py) deja `sub_con_guiones`
en el request al validar; el exception handler deja `_api_motivo` al rechazar.
"""
import logging

log = logging.getLogger("apps.api")


class ApiLoggingMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        # Solo nos interesa la API; el resto de la web no se loguea aquí.
        if request.path.startswith("/api/"):
            sub = getattr(request, "sub_con_guiones", None) or "anon"
            motivo = getattr(request, "_api_motivo", None) or "-"
            log.info(
                "api method=%s path=%s status=%s sub=%s motivo=%s",
                request.method, request.path, response.status_code, sub, motivo,
            )
        return response
