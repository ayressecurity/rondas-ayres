"""
Helpers de permisos. Leen el access token guardado en sesion
(request.session["oidc_access_token"]) para exponer roles y groups.

El token YA fue validado por el flujo OIDC (firma JWKS) al iniciar sesion;
aqui solo lo decodificamos para leer sus claims, por eso
verify_signature=False (no re-verificamos, solo leemos).

  groups = que se ve (apps/enlaces)   ·   roles = que se puede hacer
"""
import jwt


def _claims_de(request):
    """Decodifica el access token de la sesion. {} si no hay o no se puede leer."""
    token = request.session.get("oidc_access_token")
    if not token:
        return {}
    try:
        return jwt.decode(token, options={"verify_signature": False})
    except jwt.PyJWTError:
        return {}


def roles_de(request):
    """Roles del realm (claim realm_access.roles)."""
    claims = _claims_de(request)
    return (claims.get("realm_access") or {}).get("roles", []) or []


def grupos_de(request):
    """Groups del token (claim 'groups')."""
    claims = _claims_de(request)
    return claims.get("groups", []) or []


def es_super_admin(request):
    """True si el rol 'super_admin' esta presente en el token."""
    return "super_admin" in roles_de(request)
