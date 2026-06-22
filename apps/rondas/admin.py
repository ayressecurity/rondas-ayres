from django.contrib import admin

from .models import (
    Notificacion,
    Programacion,
    ProgramacionHorario,
    Ronda,
    RondaGuardia,
    RondaSecuencia,
)


@admin.register(Ronda)
class RondaAdmin(admin.ModelAdmin):
    list_display = ("id", "nombre", "instalacion_id", "cliente_id", "fecha_inicio", "estado")
    list_filter = ("estado",)


admin.site.register(RondaSecuencia)
admin.site.register(RondaGuardia)
admin.site.register(Programacion)
admin.site.register(ProgramacionHorario)
admin.site.register(Notificacion)
