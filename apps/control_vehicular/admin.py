from django.contrib import admin

from .models import Vehiculo


@admin.register(Vehiculo)
class VehiculoAdmin(admin.ModelAdmin):
    list_display = ("id", "ppu", "desplazamiento", "tipo_vehiculo", "nombre_conductor", "turno", "creado_en")
    list_filter = ("desplazamiento", "tipo_vehiculo", "turno")
    search_fields = ("ppu", "nombre_conductor", "codigo_conductor")
