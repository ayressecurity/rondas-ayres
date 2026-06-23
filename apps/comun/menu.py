"""
Menu lateral: FUENTE UNICA DE VERDAD de los modulos navegables.

Tres niveles de contexto: Cliente -> Instalacion -> Modulos.
  - BASE: siempre visibles (Inicio, Clientes).
  - + Instalaciones: solo si hay cliente seleccionado en sesion.
  - + MODULOS_INSTALACION: solo si hay instalacion seleccionada.

Cada item:
  - key / label / url_name (con namespace).
  - roles / grupos: vacio = cualquier autenticado.

Gatear un modulo por permiso mas adelante = editar SOLO este archivo.
"""
from apps.cuentas import permisos

# Siempre visibles. 'api' NO va en el menu.
MODULOS_BASE = [
    {"key": "inicio",   "label": "Inicio",   "url_name": "comun:dashboard",  "roles": [], "grupos": []},
    {"key": "clientes", "label": "Clientes", "url_name": "clientes:index",   "roles": [], "grupos": []},
]

# Solo con CLIENTE seleccionado.
MODULO_INSTALACIONES = {
    "key": "instalaciones", "label": "Instalaciones", "url_name": "instalaciones:index", "roles": [], "grupos": [],
}

# Solo con INSTALACION seleccionada (operan dentro de ella).
MODULOS_INSTALACION = [
    {"key": "checkpoints",       "label": "Puntos de control", "url_name": "checkpoints:index",       "roles": [], "grupos": []},
    {"key": "rondas",            "label": "Rondas",            "url_name": "rondas:index",            "roles": [], "grupos": []},
    {"key": "escaner",           "label": "Escáner de prueba (QR)", "url_name": "escaner:index",      "roles": ["super_admin"], "grupos": []},
    {"key": "informe_rondas",    "label": "Informe de Rondas", "url_name": "informes:rondas",         "roles": [], "grupos": []},
    {"key": "novedades",         "label": "Novedades",         "url_name": "novedades:index",         "roles": [], "grupos": []},
    {"key": "control_vehicular", "label": "Control vehicular", "url_name": "control_vehicular:index", "roles": [], "grupos": []},
    {"key": "personas",          "label": "Personas",          "url_name": "personas:index",          "roles": [], "grupos": []},
    {"key": "dispositivos",      "label": "Dispositivos",      "url_name": "dispositivos:index",      "roles": [], "grupos": []},
]


def _item_visible(item, roles_usuario, grupos_usuario, es_super):
    if not item["roles"] and not item["grupos"]:
        return True
    if es_super:
        return True
    if set(item["roles"]) & set(roles_usuario):
        return True
    if set(item["grupos"]) & set(grupos_usuario):
        return True
    return False


def menu_visible(request):
    """Devuelve SOLO los items que el usuario puede ver, marcando el activo.

    Instalaciones aparece con cliente seleccionado; los modulos de instalacion
    aparecen con instalacion seleccionada.
    """
    if not request.user.is_authenticated:
        return []

    roles_usuario = permisos.roles_de(request)
    grupos_usuario = permisos.grupos_de(request)
    es_super = permisos.es_super_admin(request)

    candidatos = list(MODULOS_BASE)
    if request.session.get("cliente_id"):
        candidatos.append(MODULO_INSTALACIONES)
    if request.session.get("instalacion_id"):
        candidatos += MODULOS_INSTALACION

    actual = getattr(request.resolver_match, "view_name", None)

    items = []
    for item in candidatos:
        if not _item_visible(item, roles_usuario, grupos_usuario, es_super):
            continue
        items.append({**item, "activo": item["url_name"] == actual})
    return items
