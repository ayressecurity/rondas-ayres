from django.contrib import admin

from .models import Cliente, Instalacion


@admin.register(Cliente)
class ClienteAdmin(admin.ModelAdmin):
    list_display = ("id", "razon_social", "rut", "estado")
    search_fields = ("razon_social", "rut", "codigo_cc")
    list_filter = ("estado",)


@admin.register(Instalacion)
class InstalacionAdmin(admin.ModelAdmin):
    list_display = ("id", "codigo", "nombre", "cliente_id", "comuna", "estado")
    search_fields = ("codigo", "nombre", "comuna")
    list_filter = ("estado", "categoria")
