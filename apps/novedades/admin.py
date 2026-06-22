from django.contrib import admin

from .models import LibroNovedades, LibroNovedadesMedia, TipoEvento


@admin.register(TipoEvento)
class TipoEventoAdmin(admin.ModelAdmin):
    list_display = ("id", "codigo", "nombre", "categoria", "activo")
    list_filter = ("categoria", "activo")
    search_fields = ("codigo", "nombre")


@admin.register(LibroNovedades)
class LibroNovedadesAdmin(admin.ModelAdmin):
    list_display = ("id", "instalacion_id", "tipo_evento", "guardia_keycloak_id", "timestamp_evento", "estado")
    list_filter = ("estado",)
    search_fields = ("guardia_keycloak_id",)


admin.site.register(LibroNovedadesMedia)
