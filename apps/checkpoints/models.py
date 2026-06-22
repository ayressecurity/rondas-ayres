"""
Modulo 4 — punto_control. Fuente: docs/rondas_schema.dbml.
instalacion_id = referencia al espejo (SIN FK).
"""
from django.db import models


class PuntoControl(models.Model):
    instalacion_id = models.BigIntegerField(db_index=True)  # (*) espejo, SIN FK
    tipo = models.CharField(max_length=40, null=True, blank=True)
    nombre = models.CharField(max_length=120)
    observacion = models.TextField(null=True, blank=True)
    lat = models.DecimalField(max_digits=10, decimal_places=8)
    lng = models.DecimalField(max_digits=11, decimal_places=8)
    tolerancia_mts = models.SmallIntegerField(default=30)
    validar_posicion = models.BooleanField(default=True)  # 0 = no validar arribo
    foto_path = models.CharField(max_length=255, null=True, blank=True)
    qr_token = models.CharField(max_length=36, unique=True)
    activo = models.BooleanField(default=True)
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "punto_control"

    def __str__(self):
        return self.nombre
