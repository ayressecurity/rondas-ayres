"""
ESPEJO de Ayres360 (réplica): cliente e instalacion. Sin FK.
id = id de Ayres (no autoincremental; se poblará por sync).
Fuente: docs/rondas_schema.dbml.
"""
from django.db import models


class EstadoAv(models.TextChoices):
    ACTIVO = "activo", "Activo"
    INACTIVO = "inactivo", "Inactivo"


class Cliente(models.Model):
    """ESPEJO de Ayres (se omite user_id). Sin FK."""
    id = models.BigIntegerField(primary_key=True)  # = id de Ayres
    razon_social = models.CharField(max_length=255)
    codigo_cc = models.CharField(max_length=20, null=True, blank=True)
    rut = models.CharField(max_length=255)
    tipo = models.CharField(max_length=255, default="privado")
    id_licitacion = models.CharField(max_length=255, null=True, blank=True)
    valor_mensual_neto = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    moneda = models.CharField(max_length=10, default="CLP")
    cantidad_admin_contrato = models.IntegerField(default=1)
    direccion = models.CharField(max_length=255, null=True, blank=True)
    email_contacto = models.CharField(max_length=255, null=True, blank=True)
    telefono_contacto = models.CharField(max_length=255, null=True, blank=True)
    nombre_contacto = models.CharField(max_length=255, null=True, blank=True)
    fecha_inicio_contrato = models.DateTimeField(null=True, blank=True)
    fecha_fin_contrato = models.DateTimeField(null=True, blank=True)
    renovacion_automatica = models.BooleanField(default=False)
    reajuste_ipc = models.BooleanField(default=False)
    reajuste_imm = models.BooleanField(default=False)
    porcentaje_reajuste_ipc = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    porcentaje_variacion_imm = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    periodicidad_reajuste = models.IntegerField(null=True, blank=True)
    estado = models.CharField(max_length=10, choices=EstadoAv.choices, default=EstadoAv.ACTIVO)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True)  # soft delete

    class Meta:
        db_table = "cliente"
        indexes = [models.Index(fields=["rut"])]

    def __str__(self):
        return self.razon_social


class Instalacion(models.Model):
    """ESPEJO de Ayres + campo propio 'codigo'. Sin FK."""
    id = models.BigIntegerField(primary_key=True)  # = id de Ayres
    codigo = models.CharField(max_length=20, unique=True)  # propio de Rondas (ej. AYR-0001)
    cliente_id = models.BigIntegerField(db_index=True)  # (*) espejo, SIN FK
    nombre = models.CharField(max_length=255)
    categoria = models.CharField(max_length=20, default="media")
    direccion = models.CharField(max_length=255, null=True, blank=True)
    region = models.CharField(max_length=255, null=True, blank=True)
    comuna = models.CharField(max_length=255, null=True, blank=True)
    latitud = models.DecimalField(max_digits=10, decimal_places=8, null=True, blank=True)
    longitud = models.DecimalField(max_digits=11, decimal_places=8, null=True, blank=True)
    dotacion_requerida = models.IntegerField(default=1)
    cantidad_guardias = models.IntegerField(default=1)
    cantidad_supervisores = models.IntegerField(default=0)
    certificaciones_requeridas = models.JSONField(null=True, blank=True)
    edad_minima_hombres = models.IntegerField(null=True, blank=True)
    edad_maxima_hombres = models.IntegerField(null=True, blank=True)
    edad_minima_mujeres = models.IntegerField(null=True, blank=True)
    edad_maxima_mujeres = models.IntegerField(null=True, blank=True)
    requisito_idioma = models.CharField(max_length=255, null=True, blank=True)
    experiencia_requerida = models.TextField(null=True, blank=True)
    capacitacion_requerida = models.TextField(null=True, blank=True)
    habilidades_especificas = models.TextField(null=True, blank=True)
    permite_ley_inclusion = models.BooleanField(default=False)
    valor_turno_extra = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    valor_jornada_ordinaria = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    estado = models.CharField(max_length=10, choices=EstadoAv.choices, default=EstadoAv.ACTIVO)
    observaciones = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True)  # soft delete

    class Meta:
        db_table = "instalacion"
        indexes = [models.Index(fields=["cliente_id"])]

    def __str__(self):
        return f"{self.codigo} · {self.nombre}"
