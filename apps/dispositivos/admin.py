from django.contrib import admin

from .models import Dispositivo


@admin.register(Dispositivo)
class DispositivoAdmin(admin.ModelAdmin):
    list_display = ("id", "instalacion_id", "nombre", "activo", "last_seen", "creado_en")
    list_filter = ("activo",)
    search_fields = ("nombre", "token_hash")
    # El hash y la fecha de alta no se editan a mano.
    readonly_fields = ("token_hash", "creado_en")
