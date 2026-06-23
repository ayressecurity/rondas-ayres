"""
Modulos 5-6 — ronda, ronda_secuencia, ronda_guardia, programacion,
programacion_horario, notificacion. Fuente: docs/rondas_schema.dbml.

FK reales SOLO entre tablas propias de Rondas (ronda_secuencia -> ronda /
punto_control; programacion -> ronda; etc.). Referencias al espejo
(cliente_id, instalacion_id) y a Keycloak (guardia_keycloak_id): por id, SIN FK.
"""
from django.db import models


class EstadoGenerico(models.TextChoices):
    ACTIVA = "activa", "Activa"
    INACTIVA = "inactiva", "Inactiva"


class RepiteRecurrencia(models.TextChoices):
    # Opciones espejo de VigiControl.
    TODOS_LOS_DIAS = "todos_los_dias", "Todos los días"
    LUNES_A_VIERNES = "lunes_a_viernes", "Lunes a Viernes"
    DIAS_SEMANA = "dias_semana", "Días de la semana"
    UNA_VEZ_AL_MES = "una_vez_al_mes", "Una vez al mes"


class DestinoNotificacion(models.TextChoices):
    GUARDIA = "guardia", "Guardia"
    GRUPO = "grupo", "Grupo"
    TODOS = "todos", "Todos"


class Ronda(models.Model):
    cliente_id = models.BigIntegerField(db_index=True)  # (*) espejo, SIN FK
    instalacion_id = models.BigIntegerField(db_index=True)  # (*) espejo, SIN FK
    nombre = models.CharField(max_length=120)
    fecha_inicio = models.DateField()
    # Modo de orden de los puntos: True = el sistema asigna orden (aleatorio al
    # crear); False = el guardia elige el orden en terreno. No viene del esquema
    # base; se agrega para recordar el modo elegido por ronda.
    orden_aleatorio = models.BooleanField(default=True)
    estado = models.CharField(max_length=10, choices=EstadoGenerico.choices, default=EstadoGenerico.ACTIVA)
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "ronda"

    def __str__(self):
        return self.nombre


class RondaSecuencia(models.Model):
    ronda = models.ForeignKey("rondas.Ronda", on_delete=models.PROTECT)
    punto_control = models.ForeignKey("checkpoints.PuntoControl", on_delete=models.PROTECT)
    orden = models.SmallIntegerField()

    class Meta:
        db_table = "ronda_secuencia"
        constraints = [
            models.UniqueConstraint(fields=["ronda", "punto_control"], name="uq_ronda_secuencia"),
        ]


class RondaGuardia(models.Model):
    ronda = models.ForeignKey("rondas.Ronda", on_delete=models.PROTECT)
    guardia_keycloak_id = models.CharField(max_length=36, db_index=True)  # (*) sub Keycloak, SIN FK

    class Meta:
        db_table = "ronda_guardia"
        constraints = [
            models.UniqueConstraint(fields=["ronda", "guardia_keycloak_id"], name="uq_ronda_guardia"),
        ]


class Programacion(models.Model):
    ronda = models.ForeignKey("rondas.Ronda", on_delete=models.PROTECT)
    repite = models.CharField(max_length=20, choices=RepiteRecurrencia.choices)
    activo = models.BooleanField(default=True)

    class Meta:
        db_table = "programacion"


class ProgramacionHorario(models.Model):
    programacion = models.ForeignKey("rondas.Programacion", on_delete=models.PROTECT)
    hora = models.PositiveSmallIntegerField()  # 0-23
    minuto = models.PositiveSmallIntegerField()  # 0-59

    class Meta:
        db_table = "programacion_horario"


class Notificacion(models.Model):
    ronda = models.ForeignKey("rondas.Ronda", on_delete=models.PROTECT)
    destino_tipo = models.CharField(max_length=10, choices=DestinoNotificacion.choices)
    destino_ref = models.CharField(max_length=80, null=True, blank=True)  # sub guardia / id grupo; null si todos
    anticipacion_min = models.SmallIntegerField()
    mensaje = models.CharField(max_length=255, null=True, blank=True)
    estado = models.CharField(max_length=10, choices=EstadoGenerico.choices, default=EstadoGenerico.ACTIVA)

    class Meta:
        db_table = "notificacion"
