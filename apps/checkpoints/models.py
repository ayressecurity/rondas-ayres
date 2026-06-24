"""
Modulo 4 — punto_control. Fuente: docs/rondas_schema.dbml.
instalacion_id = referencia al espejo (SIN FK).
"""
from django.db import models

# Tipo único de punto de control por ahora: lo fija SIEMPRE el backend (el campo
# en BD es varchar). El usuario no lo elige; todo registro nace como "Codigo QR".
TIPO_QR = "Codigo QR"


class PuntoControl(models.Model):
    instalacion_id = models.BigIntegerField(db_index=True)  # (*) espejo, SIN FK
    tipo = models.CharField(max_length=40, default=TIPO_QR, null=True, blank=True)
    nombre = models.CharField(max_length=120)
    observacion = models.TextField(null=True, blank=True)
    # Coordenadas amplias: aceptan valores muy largos (muchos decimales) sin
    # recortar, y también cortos. 18 dígitos / 12 decimales cubre cualquier
    # coordenada real con sobra (6 dígitos enteros + 12 decimales).
    lat = models.DecimalField(max_digits=18, decimal_places=12)
    lng = models.DecimalField(max_digits=18, decimal_places=12)
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
