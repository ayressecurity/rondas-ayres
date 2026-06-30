"""
Configuracion COMUN a todos los entornos (local, develop, prod).
Equivale a los archivos de config/*.php de Laravel + el uso del .env.
Lo que cambia por entorno (BD, DEBUG) se lee del .env.
"""
from pathlib import Path
import environ

# Raiz del proyecto: rondas-web/  (base.py esta en config/settings/base.py)
BASE_DIR = Path(__file__).resolve().parents[2]

# Lectura del .env
env = environ.Env(DEBUG=(bool, False))
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("SECRET_KEY", default="dev-insecure-cambia-esto")
DEBUG = env("DEBUG", default=True)
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["127.0.0.1", "localhost"])
# Origenes confiables para CSRF (deben llevar esquema, ej. https://rondas.ayressecurity.cl)
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Terceros
    "rest_framework",
    "mozilla_django_oidc",
    # Apps propias
    "apps.cuentas",
    "apps.comun",
    "apps.espejo",
    "apps.clientes",
    "apps.instalaciones",
    "apps.checkpoints",
    "apps.escaner",
    "apps.rondas",
    "apps.novedades",
    "apps.informes",
    "apps.reportar_novedad",
    "apps.control_vehicular",
    "apps.personas",
    "apps.dispositivos",
    "apps.api",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # Log estructurado de cada request a /api/ (sub, metodo, ruta, status, motivo).
    "apps.api.middleware.ApiLoggingMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                # Expone roles/groups de Keycloak a todas las plantillas.
                "apps.comun.context_processors.keycloak",
                # Contexto seleccionado (cliente + instalacion).
                "apps.comun.context_processors.contexto",
                # Version para cache-busting de estaticos (?v=...).
                "apps.comun.context_processors.estaticos",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# Base de datos: SIEMPRE MySQL, leida del .env (DATABASE_URL). Sin fallback.
# Ej.: mysql://usuario:clave@host:3306/rondas
DATABASES = {"default": env.db("DATABASE_URL")}
# Charset utf8mb4 (soporta emojis/acentos completos) en MySQL.
DATABASES["default"]["OPTIONS"] = {"charset": "utf8mb4"}

# Ayres360 (Servidor 3): MySQL externo SOLO LECTURA. Se usa unicamente via raw
# SQL con connections["ayres"].cursor() para el sync del espejo. SIN router ni
# modelos mapeados. NUNCA correr migrate sobre "ayres".
DATABASES["ayres"] = env.db("AYRES_DATABASE_URL")
DATABASES["ayres"]["OPTIONS"] = {"charset": "utf8mb4"}

# Comunas (lista separada por comas). DEPRECADO: el sync ya no filtra por comuna.
SYNC_COMUNAS = env.list("SYNC_COMUNAS", default=["las condes"])
# Razon social de los clientes a sincronizar (lista separada por comas).
# El sync trae esos clientes y TODAS sus instalaciones. Hoy: Municipalidad de Las Condes.
SYNC_CLIENTES_RAZON = env.list("SYNC_CLIENTES_RAZON", default=["municipalidad de las condes"])

# Modelo de usuario propio (debe estar ANTES de la primera migracion)
AUTH_USER_MODEL = "cuentas.Usuario"

AUTHENTICATION_BACKENDS = [
    "apps.cuentas.auth_backend.KeycloakOIDCBackend",
    "django.contrib.auth.backends.ModelBackend",
]

# ---- Keycloak / OIDC (client rondas-web-test) ----
OIDC_RP_CLIENT_ID = env("OIDC_RP_CLIENT_ID", default="rondas-web-test")
OIDC_RP_CLIENT_SECRET = env("OIDC_RP_CLIENT_SECRET", default="")
OIDC_RP_SIGN_ALGO = "RS256"
OIDC_OP_AUTHORIZATION_ENDPOINT = env("OIDC_OP_AUTHORIZATION_ENDPOINT", default="")
OIDC_OP_TOKEN_ENDPOINT = env("OIDC_OP_TOKEN_ENDPOINT", default="")
OIDC_OP_USER_ENDPOINT = env("OIDC_OP_USER_ENDPOINT", default="")
OIDC_OP_JWKS_ENDPOINT = env("OIDC_OP_JWKS_ENDPOINT", default="")
# Logout iniciado por el RP: cierra tambien la sesion SSO en Keycloak.
OIDC_OP_LOGOUT_ENDPOINT = env("OIDC_OP_LOGOUT_ENDPOINT", default="")
OIDC_OP_LOGOUT_URL_METHOD = "apps.cuentas.oidc_logout.keycloak_logout"
# Verificacion TLS al hablar con Keycloak (token endpoint + JWKS).
# Servidor (cert valido) = True. Local con cert self-signed = False (via .env).
OIDC_VERIFY_SSL = env.bool("OIDC_VERIFY_SSL", default=True)
# Guardar tokens en sesion: necesitamos el access token para leer roles/groups.
OIDC_STORE_ACCESS_TOKEN = True
OIDC_STORE_ID_TOKEN = True
LOGIN_URL = "/oidc/authenticate/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/"
# Portal (Servidor 2): destino tras cerrar sesion. Vacio = volver a Rondas.
PORTAL_URL = env("PORTAL_URL", default="")

# ---- OIDC para la API movil (validacion de JWT, stateless) ----
# La API NO usa sesion: valida el Bearer token en cada request contra el JWKS
# de Keycloak (reutiliza OIDC_OP_JWKS_ENDPOINT de arriba).
# Issuer esperado en el claim 'iss' del token.
OIDC_OP_ISSUER = env(
    "OIDC_OP_ISSUER",
    default="https://sso.ayressecurity.cl/realms/ayres-security",
)
# Audiencia esperada (claim 'aud'). Vacio = NO se valida aud (hoy probamos con
# rondas-web-test); cuando exista el client movil, se pone aqui sin tocar codigo.
OIDC_AUDIENCE = env("OIDC_AUDIENCE", default="")
# Tolerancia de reloj (clock skew) en segundos para exp/iat/nbf.
OIDC_LEEWAY_SECONDS = env.int("OIDC_LEEWAY_SECONDS", default=30)
# Cuanto cachear el JWKS en memoria (segundos) antes de re-descargarlo.
OIDC_JWKS_CACHE_SECONDS = env.int("OIDC_JWKS_CACHE_SECONDS", default=3600)

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "es-cl"
TIME_ZONE = "America/Santiago"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

# MEDIA: archivos subidos por el usuario (fotos de checkpoints, novedades...).
# En el servidor (DEBUG=False) los sirve Nginx en /media/ (ver despliegue).
# La URL lleva barra inicial para que sea absoluta (/media/...).
MEDIA_URL = env("MEDIA_URL", default="/media/")
MEDIA_ROOT = env("MEDIA_ROOT", default=BASE_DIR / "media")

# Tamaño máximo (MB) por tipo de archivo subido a la API (POST /api/eventos/{id}/media).
# Configurable por .env; defaults razonables para campo móvil.
MEDIA_MAX_FOTO_MB = env.int("MEDIA_MAX_FOTO_MB", default=10)
MEDIA_MAX_AUDIO_MB = env.int("MEDIA_MAX_AUDIO_MB", default=20)
MEDIA_MAX_VIDEO_MB = env.int("MEDIA_MAX_VIDEO_MB", default=50)

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Seguridad para el servidor (Nginx + HTTPS). Solo cuando DEBUG=False.
if not DEBUG:
    # Nginx termina el TLS y reenvia el esquema en esta cabecera.
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

# ---- Django REST Framework (API movil, stateless) ----
# La API NO usa sesion ni cookies: SOLO el Bearer token de Keycloak.
REST_FRAMEWORK = {
    # Unica forma de autenticarse en la API: nuestro portero JWT (JWKS Keycloak).
    # NO ponemos SessionAuthentication ni BasicAuthentication.
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "apps.api.authentication.KeycloakJWTAuthentication",
    ],
    # Por defecto TODO endpoint exige usuario autenticado.
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    # Solo JSON (sin el Browsable API HTML).
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
    # Errores siempre en el mismo formato JSON ({"error": {...}}).
    "EXCEPTION_HANDLER": "apps.api.exceptions.api_exception_handler",
    # Throttling NO global: solo definimos la TASA del scope 'enroll'. Se aplica
    # únicamente en la vista de enrolamiento (EnrollThrottle, vía @throttle_classes),
    # que es pública. Sin DEFAULT_THROTTLE_CLASSES => el resto de endpoints NO se
    # throttlean (siguen solo con el portero JWT).
    "DEFAULT_THROTTLE_RATES": {
        "enroll": "1/30min",  # 1 intento de enrolamiento cada 30 min por IP
    },
}

# ---- Logging ----
# Consola SIEMPRE (local/develop/prod la capturan: runserver en local; systemd
# -> journald en el servidor, se ve con `journalctl -u ayres360.service`).
# Opcional: si LOG_FILE viene en el .env, ademas escribe a archivo con rotacion
# (util en el servidor si se prefiere un fichero a journald).
LOG_FILE = env("LOG_FILE", default="")
API_LOG_LEVEL = env("API_LOG_LEVEL", default="INFO")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        # Formato legible y "grepeable": fecha nivel logger mensaje.
        "estructurado": {
            "format": "%(asctime)s %(levelname)s %(name)s %(message)s",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "estructurado",
        },
    },
    "loggers": {
        # Todo lo de la API (portero + requests) cae aqui.
        "apps.api": {
            "handlers": ["console"],
            "level": API_LOG_LEVEL,
            "propagate": False,
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "WARNING",
    },
}

# Logging a archivo con rotacion (solo si se define LOG_FILE en el .env).
# 5 MB por archivo, 5 backups. Pensado para el servidor.
if LOG_FILE:
    LOGGING["handlers"]["archivo"] = {
        "class": "logging.handlers.RotatingFileHandler",
        "filename": LOG_FILE,
        "maxBytes": 5 * 1024 * 1024,
        "backupCount": 5,
        "formatter": "estructurado",
    }
    LOGGING["loggers"]["apps.api"]["handlers"].append("archivo")
    LOGGING["root"]["handlers"].append("archivo")
