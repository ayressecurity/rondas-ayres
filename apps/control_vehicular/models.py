"""
Control vehicular — vehiculo (reemplaza el Google Form). SIN FK.
Fuente: docs/rondas_schema.dbml.
"""
from django.db import models


class DesplazamientoVehiculo(models.TextChoices):
    ENTRADA = "entrada", "Entrada"
    SALIDA = "salida", "Salida"


class TipoVehiculo(models.TextChoices):
    MOTOCICLETA = "motocicleta", "Motocicleta"
    FURGON = "furgon", "Furgón"
    AUTO = "auto", "Auto"
    STATION_WAGON = "station_wagon", "Station Wagon"
    CAMIONETA = "camioneta", "Camioneta"
    MINI_BUS = "mini_bus", "Mini Bus"


class TurnoVehiculo(models.TextChoices):
    PRIMER_TURNO = "primer_turno", "1er turno"
    SEGUNDO_TURNO = "segundo_turno", "2do turno"
    TERCER_TURNO = "tercer_turno", "3er turno"
    INTERMEDIO = "intermedio", "Intermedio"
    TURNO_LARGO = "turno_largo", "Turno largo"
    TURNO_ESPECIAL = "turno_especial", "Turno especial"


class Vehiculo(models.Model):
    desplazamiento = models.CharField(max_length=10, choices=DesplazamientoVehiculo.choices)
    recinto = models.CharField(max_length=120)
    ppu = models.CharField(max_length=30)
    kilometraje = models.IntegerField(null=True, blank=True)
    tipo_vehiculo = models.CharField(max_length=20, choices=TipoVehiculo.choices)
    nombre_conductor = models.CharField(max_length=160)
    codigo_conductor = models.CharField(max_length=50, null=True, blank=True)
    turno = models.CharField(max_length=20, choices=TurnoVehiculo.choices)
    registrado_keycloak_id = models.CharField(max_length=36, null=True, blank=True, db_index=True)  # (*) quien registro
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "vehiculo"

    def __str__(self):
        return f"{self.ppu} ({self.get_desplazamiento_display()})"
