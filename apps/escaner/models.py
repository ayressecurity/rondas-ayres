"""
Modelo del SIMULADOR DE EJECUCIÓN DE RONDA (app escaner).

ronda_ejecucion = una "corrida" de una ronda por un guardia: registra cuándo la
inició, si sigue en curso o ya la completó. Los escaneos quedan en
libro_novedades (con ronda_id = la ronda de la ejecución en curso).

FK real solo a tabla propia de Rondas (ronda). instalacion_id y
guardia_keycloak_id son referencias por id, SIN FK (espejo / Keycloak).
"""
from django.db import models


class RondaEjecucion(models.Model):
    class Estado(models.TextChoices):
        EN_CURSO = "en_curso", "En curso"
        COMPLETADA = "completada", "Completada"

    ronda = models.ForeignKey("rondas.Ronda", on_delete=models.PROTECT)
    guardia_keycloak_id = models.CharField(max_length=36, db_index=True)  # (*) sub Keycloak
    instalacion_id = models.BigIntegerField(db_index=True)  # (*) espejo, SIN FK
    iniciada_en = models.DateTimeField(auto_now_add=True)
    finalizada_en = models.DateTimeField(null=True, blank=True)
    estado = models.CharField(max_length=12, choices=Estado.choices, default=Estado.EN_CURSO)

    class Meta:
        db_table = "ronda_ejecucion"

    def __str__(self):
        return f"Ejecución #{self.pk} de ronda {self.ronda_id} ({self.estado})"
