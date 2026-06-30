"""
Modelo Dispositivo: teléfonos enrolados a una instalación mediante el QR fijo.

Es la identidad del DISPOSITIVO (da la instalación), SEPARADA de la del GUARDIA
(token Keycloak). En este módulo solo se enrola el dispositivo; usar el
X-Device-Token en las marcas y llenar libro_novedades.dispositivo_id es una fase
posterior.

Referencia a instalacion por id LÓGICO, SIN ForeignKey (regla del proyecto: nada
apunta al espejo de Ayres con FK). El token del dispositivo NUNCA se guarda en
claro: solo su hash SHA-256.
"""
from datetime import timedelta

from django.db import models
from django.utils import timezone

# Throttle de last_seen: no escribimos en cada request (libro_novedades/marcas son
# de alto volumen); basta con refrescar la presencia cada 5 minutos.
TOUCH_THROTTLE = timedelta(minutes=5)


class Dispositivo(models.Model):
    """Un teléfono enrolado y amarrado a una instalación."""

    # (*) referencia LÓGICA a instalacion.id (espejo de Ayres). SIN FK, indexada.
    instalacion_id = models.BigIntegerField(db_index=True)
    # SHA-256 hex (64 chars) del token del dispositivo. Único: identifica al
    # teléfono en las marcas. El token en claro solo se entrega una vez (show-once).
    token_hash = models.CharField(max_length=64, unique=True, db_index=True)
    nombre = models.CharField(max_length=120, blank=True)   # etiqueta para que el SSPP lo reconozca
    activo = models.BooleanField(default=True)              # revocar = soft (activo=False); nunca se borra
    device_info = models.JSONField(null=True, blank=True)   # modelo/versión del teléfono (opcional)
    last_seen = models.DateTimeField(null=True, blank=True)
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "dispositivo"

    def __str__(self):
        return self.nombre or f"Dispositivo {self.pk}"

    def touch(self):
        """Refresca last_seen (presencia del dispositivo) con throttle de 5 min.

        Lo llama el authenticator en cada request del dispositivo; el throttle
        evita un UPDATE por petición. Hora del servidor (America/Santiago)."""
        ahora = timezone.now()
        if self.last_seen is None or (ahora - self.last_seen) > TOUCH_THROTTLE:
            self.last_seen = ahora
            self.save(update_fields=["last_seen"])
