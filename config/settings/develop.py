"""
Entorno DEVELOP (servidor de desarrollo). MySQL via .env del servidor.
Hereda de base y lee TODO del .env: DEBUG, ALLOWED_HOSTS, CSRF_TRUSTED_ORIGINS,
DATABASE_URL (MySQL) y OIDC_*. No es produccion: usa el client OIDC de develop.
"""
from .base import *  # noqa
