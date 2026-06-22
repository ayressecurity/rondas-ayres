"""
persona — PENDIENTE (solo PK por ahora). Aparte de usuario y de guardia.
Sin relaciones. Fuente: docs/rondas_schema.dbml.
"""
from django.db import models


class Persona(models.Model):
    """POR DETALLAR. Solo PK por ahora."""

    class Meta:
        db_table = "persona"
