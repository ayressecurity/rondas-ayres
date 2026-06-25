"""
Normalización ÚNICA del keycloak_id (claim 'sub').

CONTRATO DE IDENTIDAD del proyecto:
- El 'sub' del token de Keycloak viene CON guiones (UUID canónico).
- En cuentas_usuario.keycloak_id se guarda SIN guiones (lo hace el UUIDField).
- En libro_novedades/ronda_*/vehiculo se guarda el sub TAL CUAL (CON guiones).

Para COMPARAR/BUSCAR filas hay que poner ambos lados en el mismo formato:
sin guiones y en minúsculas. Esa lógica vive SOLO aquí; el resto del código
(informes, portero de la API, etc.) importa esta función. No duplicar.
"""


def norm_keycloak_id(valor) -> str:
    """Normaliza un keycloak_id/sub para comparar: sin guiones, en minúsculas."""
    return (str(valor) if valor is not None else "").replace("-", "").lower()
