"""
Serializers de la API móvil (solo lectura). Campos EXPLÍCITOS: no exponemos
columnas de más (observación, foto_path, creado_en, etc.). La identidad nunca
viaja en estas respuestas: ya salió del token en el portero.
"""
from rest_framework import serializers

from apps.checkpoints.models import PuntoControl
from apps.rondas.models import Notificacion, Ronda, RondaSecuencia


class PuntoControlByQrSerializer(serializers.ModelSerializer):
    """Datos del punto que el móvil necesita al escanear un QR.

    lat/lng como float (no string): es como los consume el escáner para el
    haversine. instalacion_id es referencia al espejo (sin FK)."""
    lat = serializers.FloatField()
    lng = serializers.FloatField()

    class Meta:
        model = PuntoControl
        fields = [
            "id", "nombre", "instalacion_id",
            "lat", "lng", "tolerancia_mts", "validar_posicion", "activo",
        ]


class RondaSecuenciaSerializer(serializers.ModelSerializer):
    """Un punto dentro de la ruta de la ronda: qué punto y en qué orden."""
    class Meta:
        model = RondaSecuencia
        fields = ["punto_control_id", "orden"]


class RondaSerializer(serializers.ModelSerializer):
    """Ronda asignada al guardia + su secuencia de puntos (para armar la ruta)."""
    # La secuencia ya viene ordenada por 'orden' desde el Prefetch de la vista.
    secuencia = RondaSecuenciaSerializer(source="rondasecuencia_set", many=True, read_only=True)

    class Meta:
        model = Ronda
        fields = [
            "id", "nombre", "instalacion_id",
            "hora_inicio", "hora_fin", "orden_aleatorio", "estado",
            "secuencia",
        ]


class NotificacionSerializer(serializers.ModelSerializer):
    """Recordatorio que aplica al guardia. No exponemos destino_ref (interno)."""
    class Meta:
        model = Notificacion
        fields = [
            "id", "ronda_id", "destino_tipo",
            "anticipacion_min", "mensaje", "estado",
        ]


class EventoCreateSerializer(serializers.Serializer):
    """Body de POST /api/eventos (una marca del guardia desde la app móvil).

    SOLO estos campos. La identidad (guardia) sale del TOKEN, NUNCA del body; la
    instalación se DERIVA del punto del qr_token. Si el body trae keycloak_id /
    guardia / sub / instalacion_id, se ignoran (no están declarados aquí)."""
    qr_token = serializers.CharField(max_length=36)
    # Misma precisión que PuntoControl/LibroNovedades. Se exigen numéricos.
    lat = serializers.DecimalField(max_digits=20, decimal_places=17)
    lng = serializers.DecimalField(max_digits=20, decimal_places=17)
    # Hora de TERRENO (offline). Opcional: si no viene, el servidor usa "ahora".
    timestamp_evento = serializers.DateTimeField(required=False, allow_null=True)
    texto = serializers.CharField(required=False, allow_blank=True, allow_null=True)
