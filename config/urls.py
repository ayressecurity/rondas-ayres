"""Rutas raiz del proyecto. Equivale a routes/web.php de Laravel."""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path("admin/", admin.site.urls),
    path("oidc/", include("mozilla_django_oidc.urls")),  # login SSO (Keycloak)
    # Modulos (esqueleto: urls aun vacias, pero ya enganchadas)
    path("clientes/", include("apps.clientes.urls")),
    path("instalaciones/", include("apps.instalaciones.urls")),
    path("checkpoints/", include("apps.checkpoints.urls")),
    path("escaner/", include("apps.escaner.urls")),
    path("reportar-novedad/", include("apps.reportar_novedad.urls")),
    path("rondas/", include("apps.rondas.urls")),
    path("novedades/", include("apps.novedades.urls")),
    path("informes/", include("apps.informes.urls")),
    path("control-vehicular/", include("apps.control_vehicular.urls")),
    path("personas/", include("apps.personas.urls")),
    path("dispositivos/", include("apps.dispositivos.urls")),
    path("eventos-tiempo-real/", include("apps.tiempo_real.urls")),
    path("api/", include("apps.api.urls")),
    # Dashboard / home
    path("", include("apps.comun.urls")),
]

# En dev (DEBUG) Django sirve los archivos de MEDIA; en prod lo hace Nginx.
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
