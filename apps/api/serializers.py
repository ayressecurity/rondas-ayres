"""
Serializers de la API móvil (solo lectura). Campos EXPLÍCITOS: no exponemos
columnas de más (observación, foto_path, creado_en, etc.). La identidad nunca
viaja en estas respuestas: ya salió del token en el portero.
"""
from django.utils import timezone
from rest_framework import serializers

from apps.checkpoints.models import PuntoControl
from apps.comun.services.rondas import ventanas_de_alarma
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
    """Un punto dentro de la ruta de la ronda: qué punto, en qué orden y su nombre.

    `nombre` sale de punto_control.nombre (la vista lo trae con select_related para
    no disparar N+1)."""
    nombre = serializers.CharField(source="punto_control.nombre", read_only=True)

    class Meta:
        model = RondaSecuencia
        fields = ["orden", "punto_control_id", "nombre"]


class RondaSerializer(serializers.ModelSerializer):
    """Ronda + turno + programación (alarmas/vueltas) + secuencia de puntos.

    `programacion` es null si la ronda no tiene programación activa. Los estados de
    cada vuelta son TEMPORALES (según la hora actual): completada/activa/pendiente.
    """
    # La secuencia ya viene ordenada por 'orden' desde el Prefetch de la vista.
    secuencia = RondaSecuenciaSerializer(source="rondasecuencia_set", many=True, read_only=True)
    cruza_medianoche = serializers.SerializerMethodField()
    programacion = serializers.SerializerMethodField()

    class Meta:
        model = Ronda
        fields = [
            "id", "nombre", "instalacion_id",
            "hora_inicio", "hora_fin", "orden_aleatorio", "estado",
            "cruza_medianoche", "programacion", "secuencia",
        ]

    def get_cruza_medianoche(self, ronda):
        """True si el turno cruza medianoche (hora_inicio > hora_fin)."""
        hi, hf = ronda.hora_inicio, ronda.hora_fin
        return bool(hi and hf and hi > hf)

    def _horarios_precargados(self, ronda):
        """Aplana los ProgramacionHorario de las programaciones ACTIVAS ya
        prefetcheadas por la vista (sin tocar la BD)."""
        return [
            h
            for prog in ronda.programacion_set.all()        # prefetch: solo activo=True
            for h in prog.programacionhorario_set.all()     # prefetch: order_by('orden')
        ]

    def get_programacion(self, ronda):
        """Alarmas fusionadas en una línea de tiempo, con estado temporal por vuelta.

        null si no hay programación activa (o la ronda no tiene rango horario)."""
        ahora = self.context.get("ahora") or timezone.now()
        horarios = self._horarios_precargados(ronda)
        ventanas = ventanas_de_alarma(ronda, ahora, horarios=horarios)
        if not ventanas:
            return None

        ahora_local = timezone.localtime(ahora)
        vueltas = []
        vuelta_actual = None
        for i, (h, inicio, fin) in enumerate(ventanas, start=1):
            if inicio <= ahora_local <= fin:
                estado = "activa"
                vuelta_actual = i
            elif inicio > ahora_local:
                estado = "pendiente"
            else:
                estado = "completada"
            vueltas.append({"orden": h.orden, "hora": f"{h.hora:02d}:{h.minuto:02d}", "estado": estado})

        # repite: el de la 1ª programación activa (todas comparten ronda/turno).
        repite = next((p.repite for p in ronda.programacion_set.all()), None)
        return {
            "repite": repite,
            "total_vueltas": len(vueltas),
            "vuelta_actual": vuelta_actual,
            "horarios": vueltas,
        }


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
    qr_token = serializers.CharField(
        max_length=36,
        error_messages={"required": "Falta el qr_token.", "blank": "El qr_token no puede estar vacío."},
    )
    # Misma precisión que PuntoControl/LibroNovedades. Se exigen numéricos.
    lat = serializers.DecimalField(
        max_digits=20, decimal_places=17,
        error_messages={"required": "Falta la latitud (lat).", "invalid": "La latitud debe ser numérica."},
    )
    lng = serializers.DecimalField(
        max_digits=20, decimal_places=17,
        error_messages={"required": "Falta la longitud (lng).", "invalid": "La longitud debe ser numérica."},
    )
    # Hora de TERRENO (offline). Opcional: si no viene, el servidor usa "ahora".
    timestamp_evento = serializers.DateTimeField(required=False, allow_null=True)
    texto = serializers.CharField(required=False, allow_blank=True, allow_null=True)

    def validate_lat(self, value):
        if not (-90 <= value <= 90):
            raise serializers.ValidationError("Latitud fuera de rango (-90 a 90).")
        return value

    def validate_lng(self, value):
        if not (-180 <= value <= 180):
            raise serializers.ValidationError("Longitud fuera de rango (-180 a 180).")
        return value
