from django.contrib import admin

from .models import PuntoControl


@admin.register(PuntoControl)
class PuntoControlAdmin(admin.ModelAdmin):
    list_display = ("id", "nombre", "instalacion_id", "tipo", "activo")
    search_fields = ("nombre", "qr_token")
    list_filter = ("activo", "validar_posicion")
