"""
Backend OIDC para Keycloak. NO hay login propio: el usuario llega autenticado
por el SSO unico y aqui solo lo vinculamos a la fila local por el 'sub' (UUID).
Ademas leemos del token los roles y groups: si trae el rol 'super_admin'
le damos acceso total al admin de Django (is_staff/is_superuser).
"""
from mozilla_django_oidc.auth import OIDCAuthenticationBackend


def _roles_de_claims(claims):
    """Roles del realm: claim realm_access.roles (lista)."""
    return (claims.get("realm_access") or {}).get("roles", []) or []


def _grupos_de_claims(claims):
    """Groups del token: claim 'groups' (lista)."""
    return claims.get("groups", []) or []


class KeycloakOIDCBackend(OIDCAuthenticationBackend):
    def filter_users_by_claims(self, claims):
        sub = claims.get("sub")
        if not sub:
            return self.UserModel.objects.none()
        return self.UserModel.objects.filter(keycloak_id=sub)

    def _aplicar_permisos(self, user, claims):
        """is_staff/is_superuser segun el rol 'super_admin' del realm."""
        es_super = "super_admin" in _roles_de_claims(claims)
        user.is_staff = es_super
        user.is_superuser = es_super

    def create_user(self, claims):
        user = self.UserModel.objects.create_user(
            username=claims.get("preferred_username") or claims.get("sub"),
            email=claims.get("email", ""),
        )
        user.keycloak_id = claims.get("sub")
        user.first_name = claims.get("given_name", "")
        user.last_name = claims.get("family_name", "")
        self._aplicar_permisos(user, claims)
        user.save()
        return user

    def update_user(self, user, claims):
        user.keycloak_id = claims.get("sub")
        user.email = claims.get("email", user.email)
        self._aplicar_permisos(user, claims)
        user.save()
        return user
