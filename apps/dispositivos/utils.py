"""
Utilidades de enrolamiento. Funciones puras, sin estado y testeables.

Dos secretos distintos:
  - SECRETO del QR (instalacion.qr): texto recuperable. El SSPP reimprime el
    cartel cuando quiera, por eso vive en claro en instalacion.qr.
  - TOKEN del dispositivo: NUNCA se guarda en claro; solo su hash SHA-256. Se
    entrega al teléfono una sola vez en el enrolamiento (show-once).
"""
import hashlib
import secrets

# 32 bytes -> ~43 chars URL-safe. Cabe en los CharField(max_length=64) y deja
# margen. Alta entropía: no es fuerza-bruteable.
_BYTES_ENTROPIA = 32


def generar_secreto():
    """Secreto de enrolamiento de la instalación (alta entropía, URL-safe)."""
    return secrets.token_urlsafe(_BYTES_ENTROPIA)


def generar_token():
    """Token individual de un dispositivo (alta entropía, URL-safe)."""
    return secrets.token_urlsafe(_BYTES_ENTROPIA)


def hash_token(token):
    """SHA-256 hex (64 chars) del token. Es lo único que se persiste."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
