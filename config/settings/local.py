"""
Entorno LOCAL (solo mi PC). SQLite, sin MySQL ni Docker.
NUNCA va a un servidor: develop/prod usan MySQL via .env (base.py) y no se tocan.
Hereda de base y SOLO sobreescribe lo necesario para correr local.
"""
from .base import *  # noqa

DEBUG = True
ALLOWED_HOSTS = ["127.0.0.1", "localhost"]

# SQLite local (sin instalar nada). Sobreescribe el MySQL de base.py.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}
