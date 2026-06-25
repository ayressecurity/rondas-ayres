"""
PORTERO de la API móvil: autenticación por JWT de Keycloak (stateless).

NO hay sesión ni cookies. En CADA request se valida el header
`Authorization: Bearer <token>`:

  header Bearer  ->  JWKS (firma)  ->  validación (exp/iss/aud)  ->  usuario local (JIT)

La firma se verifica con la llave pública de Keycloak (JWKS), cacheada en memoria.
El usuario local se resuelve por el 'sub' del token (alta JIT si no existe).

Comparar con Laravel: equivale a un guard 'api' que valida un JWT firmado por un
proveedor externo y hace "find-or-create" del usuario por su UUID.
"""
import json
import logging
import threading
import time

import jwt
import requests
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import CharField, Value
from django.db.models.functions import Cast, Lower, Replace
from jwt.algorithms import RSAAlgorithm
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed

# Reutilizamos la lectura de roles del realm del backend SSO (no duplicar).
from apps.cuentas.auth_backend import _roles_de_claims
from apps.cuentas.identidad import norm_keycloak_id
from apps.api.exceptions import DependenciaNoDisponible

log = logging.getLogger("apps.api")


# ---------------------------------------------------------------------------
# Caché del JWKS (llaves públicas de Keycloak) en memoria del proceso.
# {kid: llave_publica}. Se refresca cuando vence o cuando llega un kid nuevo.
# ---------------------------------------------------------------------------
_lock = threading.Lock()
_cache = {"at": 0.0, "por_kid": {}}  # at = time.monotonic() de la última carga


def _descargar_jwks():
    """Descarga el JWKS de Keycloak y devuelve {kid: llave_publica RSA}.

    Lanza DependenciaNoDisponible (-> 503) si Keycloak no responde/timeout.
    """
    url = settings.OIDC_OP_JWKS_ENDPOINT
    try:
        resp = requests.get(url, timeout=5, verify=settings.OIDC_VERIFY_SSL)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as e:
        # No filtramos la traza al cliente; sí la dejamos en el log del servidor.
        log.warning("jwks_no_disponible url=%s error=%s", url, e)
        raise DependenciaNoDisponible() from e

    por_kid = {}
    for jwk in data.get("keys", []):
        if jwk.get("kty") != "RSA":
            continue  # solo nos sirven las RSA (firma RS256)
        kid = jwk.get("kid")
        if kid:
            por_kid[kid] = RSAAlgorithm.from_jwk(json.dumps(jwk))
    return por_kid


def _refrescar():
    """Recarga el JWKS y actualiza la caché (bajo lock)."""
    _cache["por_kid"] = _descargar_jwks()
    _cache["at"] = time.monotonic()


def obtener_llave(kid):
    """Devuelve la llave pública para ese 'kid', o None si no existe.

    - Si la caché está vacía o vencida (OIDC_JWKS_CACHE_SECONDS) -> recarga.
    - Si el 'kid' no está (Keycloak pudo rotar llaves) -> recarga UNA vez más.
    - None => el token trae un kid que Keycloak no reconoce -> token inválido.
    """
    with _lock:
        vencida = (time.monotonic() - _cache["at"]) >= settings.OIDC_JWKS_CACHE_SECONDS
        if not _cache["por_kid"] or vencida:
            _refrescar()
        llave = _cache["por_kid"].get(kid)
        if llave is None:
            _refrescar()  # refresco único por kid desconocido
            llave = _cache["por_kid"].get(kid)
        return llave


# ---------------------------------------------------------------------------
# Resolución del usuario local a partir de los claims (alta JIT).
# ---------------------------------------------------------------------------
def resolver_usuario_jit(claims):
    """Devuelve (usuario_local, creado_jit: bool) a partir del token.

    - BÚSQUEDA: por keycloak_id normalizado (sin guiones, minúsculas) en AMBOS
      lados, igual que los informes. El 'sub' viene con guiones; la fila guarda
      el UUID sin guiones (UUIDField).
    - ALTA JIT (decisión cerrada = a): si no existe, se crea al vuelo con la
      identidad del token, SIEMPRE con is_staff=False / is_superuser=False.
      Los permisos NO salen de esta fila: salen de los roles del token.
    """
    Usuario = get_user_model()
    sub = claims.get("sub")
    objetivo = norm_keycloak_id(sub)

    # keycloak_id es UUIDField (texto sin guiones en BD). Lo normalizamos en SQL
    # para igualar al objetivo ya normalizado.
    kc_texto = Cast("keycloak_id", output_field=CharField())
    kc_norm = Lower(Replace(kc_texto, Value("-"), Value(""), output_field=CharField()))
    user = (
        Usuario.objects
        .annotate(kc_norm=kc_norm)
        .filter(kc_norm=objetivo)
        .first()
    )
    if user is not None:
        return user, False

    # Alta JIT con identidad básica del token.
    user = Usuario(
        username=claims.get("preferred_username") or sub,
        email=claims.get("email", "") or "",
        first_name=claims.get("given_name", "") or "",
        last_name=claims.get("family_name", "") or "",
        keycloak_id=sub,          # el UUIDField lo guarda sin guiones
        is_staff=False,
        is_superuser=False,
    )
    user.set_unusable_password()  # no hay login local: la auth es por token
    user.save()
    log.info("usuario_jit_creado sub=%s username=%s", sub, user.username)
    return user, True


# ---------------------------------------------------------------------------
# Errores de autenticación con "motivo" para el log estructurado.
# ---------------------------------------------------------------------------
def _falla(mensaje, motivo):
    """AuthenticationFailed (-> 401) etiquetada con un motivo para loguear."""
    exc = AuthenticationFailed(mensaje)
    exc.motivo = motivo  # lo leen el exception handler y el middleware de log
    return exc


class KeycloakJWTAuthentication(BaseAuthentication):
    """Valida el Bearer token de Keycloak. Sin token -> None (DRF responde 401)."""

    keyword = "Bearer"

    def authenticate(self, request):
        # 1) Header Authorization: Bearer <token>
        auth = get_authorization_header(request).split()
        if not auth or auth[0].lower() != self.keyword.lower().encode():
            # Sin header o no es Bearer: no autenticamos aquí. DRF + IsAuthenticated
            # devolverá 401 (NotAuthenticated). NO lanzamos 500.
            return None
        if len(auth) == 1:
            raise _falla("Token ausente.", "token_ausente")
        if len(auth) > 2:
            raise _falla("Token mal formado.", "token_malformado")
        token = auth[1].decode("latin-1")

        # 2) kid del header -> llave pública del JWKS (puede lanzar 503)
        try:
            cabecera = jwt.get_unverified_header(token)
        except jwt.PyJWTError:
            raise _falla("Token inválido.", "token_malformado")
        llave = obtener_llave(cabecera.get("kid"))
        if llave is None:
            raise _falla("Token inválido.", "kid_desconocido")

        # 3) Validación: firma RS256 + exp/iat/nbf (leeway) + iss (+ aud opcional)
        audiencia = settings.OIDC_AUDIENCE or None
        try:
            claims = jwt.decode(
                token,
                llave,
                algorithms=["RS256"],
                issuer=settings.OIDC_OP_ISSUER,
                audience=audiencia,
                leeway=settings.OIDC_LEEWAY_SECONDS,
                options={
                    "require": ["exp", "iat"],
                    # Solo exigimos aud si configuramos OIDC_AUDIENCE.
                    "verify_aud": audiencia is not None,
                },
            )
        except jwt.ExpiredSignatureError:
            raise _falla("Token expirado.", "token_expirado")
        except jwt.InvalidIssuerError:
            raise _falla("Token inválido.", "issuer_invalido")
        except jwt.InvalidAudienceError:
            raise _falla("Token inválido.", "audiencia_invalida")
        except jwt.PyJWTError:
            raise _falla("Token inválido.", "token_invalido")

        # 4) Usuario local (JIT si no existe)
        user, creado_jit = resolver_usuario_jit(claims)

        # Dejamos disponible en request lo que necesitan las vistas:
        #  - sub_con_guiones: para ESCRIBIR en otras tablas (libro_novedades...)
        #  - auth_claims: el token decodificado (roles, email, etc.)
        #  - creado_jit: si la fila se acaba de crear (lo usa /api/me)
        # Lo guardamos en el HttpRequest subyacente (request._request) para que el
        # middleware de log también lo vea; la vista (DRF Request) lo lee igual,
        # porque DRF delega los atributos desconocidos a _request.
        base = getattr(request, "_request", request)
        base.auth_claims = claims
        base.sub_con_guiones = claims.get("sub")
        base.token_roles = _roles_de_claims(claims)
        base.creado_jit = creado_jit

        # DRF deja esto en request.user y request.auth.
        return (user, token)

    def authenticate_header(self, request):
        # Presencia de este header hace que DRF responda 401 (no 403) sin auth.
        return 'Bearer realm="api"'
