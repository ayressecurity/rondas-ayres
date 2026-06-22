"""Rutas raiz del proyecto. Equivale a routes/web.php de Laravel."""
from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path("admin/", admin.site.urls),
    path("oidc/", include("mozilla_django_oidc.urls")),  # login SSO (Keycloak)
    # Modulos (esqueleto: urls aun vacias, pero ya enganchadas)
    path("clientes/", include("apps.clientes.urls")),
    path("instalaciones/", include("apps.instalaciones.urls")),
    path("checkpoints/", include("apps.checkpoints.urls")),
    path("rondas/", include("apps.rondas.urls")),
    path("novedades/", include("apps.novedades.urls")),
    path("control-vehicular/", include("apps.control_vehicular.urls")),
    path("personas/", include("apps.personas.urls")),
    path("dispositivos/", include("apps.dispositivos.urls")),
    path("api/", include("apps.api.urls")),
    # Dashboard / home
    path("", include("apps.comun.urls")),
]
