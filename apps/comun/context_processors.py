"""
Context processors globales (como View Composers globales de Laravel).
Registrados en TEMPLATES["OPTIONS"]["context_processors"].
"""
from pathlib import Path

from django.conf import settings

from apps.cuentas import permisos
from apps.comun import menu


def keycloak(request):
    """Roles/groups de Keycloak + menu lateral visible para el usuario."""
    return {
        "kc_roles": permisos.roles_de(request),
        "kc_grupos": permisos.grupos_de(request),
        "es_super_admin": permisos.es_super_admin(request),
        "menu": menu.menu_visible(request),
    }


def contexto(request):
    """Contexto seleccionado (cliente + instalacion) + enlace al portal."""
    return {
        "cliente_id": request.session.get("cliente_id"),
        "cliente_nombre": request.session.get("cliente_nombre"),
        "instalacion_id": request.session.get("instalacion_id"),
        "instalacion_nombre": request.session.get("instalacion_nombre"),
        "portal_url": settings.PORTAL_URL,
    }


def estaticos(request):
    """
    Version para cache-busting de los estaticos (?v=...).
    Usa el mtime del CSS: al editarlo, el navegador lo vuelve a pedir solo,
    sin necesidad de hard-refresh. En prod, ademas, los nombres van con hash.
    """
    css = Path(settings.BASE_DIR) / "static" / "css" / "dashboard.css"
    try:
        version = int(css.stat().st_mtime)
    except OSError:
        version = 0
    return {"STATIC_VERSION": version}
