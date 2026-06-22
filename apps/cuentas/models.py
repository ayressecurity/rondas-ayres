"""
Usuario propio de Rondas. Extiende el de Django (AbstractUser) y agrega
keycloak_id (= claim 'sub' del token), zona y turno. La relacion con otros
sistemas es LOGICA por UUID, sin FK cruzada.
Equivale a un modelo Eloquent + su migracion, juntos.

analizar mas profundamente si seguir con zona y turno como campos del usuario, o si hacer modelos separados (Zona, Turno) y FK a esos modelos. La ventaja de lo segundo es que se puede agregar info extra a cada zona/turno, y evitar errores de tipeo. La ventaja de lo primero es que es mas simple y directo, y no se espera que haya mucha variacion en zonas/turnos.
"""
from django.contrib.auth.models import AbstractUser
from django.db import models


class Usuario(AbstractUser):
    keycloak_id = models.UUIDField(unique=True, null=True, blank=True, db_index=True)
    zona = models.CharField(max_length=80, blank=True)
    turno = models.CharField(max_length=20, blank=True)

    def __str__(self):
        return self.username
