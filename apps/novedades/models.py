"""
Modulos 7-8 — tipo_evento (catalogo), libro_novedades, libro_novedades_media.
Fuente: docs/rondas_schema.dbml.

FK reales SOLO a tablas propias de Rondas (libro_novedades -> ronda /
punto_control / tipo_evento; libro_novedades_media -> libro_novedades).
instalacion_id y guardia_keycloak_id: referencias por id, SIN FK.
libro_novedades es la tabla CALIENTE: indice (instalacion_id, timestamp_evento).
"""
from django.db import models


class CategoriaEvento(models.TextChoices):
    SESION = "sesion", "Sesión"
    RONDA = "ronda", "Ronda"
    NOVEDAD = "novedad", "Novedad"
    ERROR = "error", "Error"


class EstadoEvento(models.TextChoices):
    OK = "ok", "OK"
    ALERTA = "alerta", "Alerta"
    ERROR = "error", "Error"


class TipoMedia(models.TextChoices):
    FOTO = "foto", "Foto"
    AUDIO = "audio", "Audio"
    VIDEO = "video", "Video"


class TipoEvento(models.Model):
    """Catalogo de eventos."""
    id = models.SmallAutoField(primary_key=True)  # smallint increment
    codigo = models.CharField(max_length=20, unique=True)
    nombre = models.CharField(max_length=60)
    categoria = models.CharField(max_length=10, choices=CategoriaEvento.choices)
    activo = models.BooleanField(default=True)

    class Meta:
        db_table = "tipo_evento"

    def __str__(self):
        return f"{self.codigo} · {self.nombre}"


class LibroNovedades(models.Model):
    instalacion_id = models.BigIntegerField()  # (*) espejo, SIN FK; va en indice compuesto
    ronda = models.ForeignKey("rondas.Ronda", on_delete=models.PROTECT, null=True, blank=True)
    punto_control = models.ForeignKey("checkpoints.PuntoControl", on_delete=models.PROTECT, null=True, blank=True)
    guardia_keycloak_id = models.CharField(max_length=36, db_index=True)  # (*) sub Keycloak, SIN FK
    dispositivo_id = models.BigIntegerField(null=True, blank=True)  # FK pendiente (modulo dispositivos)
    tipo_evento = models.ForeignKey("novedades.TipoEvento", on_delete=models.PROTECT)
    timestamp_evento = models.DateTimeField()  # terreno
    timestamp_servidor = models.DateTimeField()  # offline / al sincronizar
    lat = models.DecimalField(max_digits=10, decimal_places=8, null=True, blank=True)
    lng = models.DecimalField(max_digits=11, decimal_places=8, null=True, blank=True)
    distancia_metros = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True)
    dentro_geocerca = models.BooleanField(null=True, blank=True)
    estado = models.CharField(max_length=10, choices=EstadoEvento.choices, default=EstadoEvento.OK)
    texto = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "libro_novedades"
        indexes = [
            models.Index(fields=["instalacion_id", "timestamp_evento"]),
        ]


class LibroNovedadesMedia(models.Model):
    libro_novedades = models.ForeignKey(
        "novedades.LibroNovedades", on_delete=models.PROTECT, related_name="medios"
    )
    tipo = models.CharField(max_length=10, choices=TipoMedia.choices)
    path = models.CharField(max_length=255)
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "libro_novedades_media"
