"""
RP-initiated logout contra Keycloak. mozilla-django-oidc llama esta funcion
ANTES de limpiar la sesion de Django, asi que el id_token aun esta disponible
para pasarlo como id_token_hint. Devuelve la URL del logout endpoint de
Keycloak, que cierra la sesion SSO y luego redirige a LOGOUT_REDIRECT_URL.
"""
from urllib.parse import urlencode

from django.conf import settings


def keycloak_logout(request):
    id_token = request.session.get("oidc_id_token")
    # Tras el logout, volver al PORTAL si esta configurado; si no, a Rondas.
    destino = settings.PORTAL_URL or request.build_absolute_uri(settings.LOGOUT_REDIRECT_URL)
    params = {
        "post_logout_redirect_uri": destino,
        "client_id": settings.OIDC_RP_CLIENT_ID,
    }
    if id_token:
        params["id_token_hint"] = id_token
    return f"{settings.OIDC_OP_LOGOUT_ENDPOINT}?{urlencode(params)}"
