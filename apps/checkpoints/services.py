"""
Service de configuración de puntos de control por escaneo de QR.

Lógica ÚNICA compartida por la web ("Configurar QR": configurar_qr_guardar) y la
API móvil (POST /api/qr/configurar): validar lat/lng/tolerancia/no_validar y
aplicar el UPDATE al punto. Extraída TAL CUAL de la vista web (mover, no
reescribir): mismas validaciones, mismos mensajes y mismo guardado
(update_fields = lat, lng, validar_posicion, tolerancia_mts).

Aquí NO hay request/session/JsonResponse: el llamador resuelve el punto y su
permiso; este service solo valida los datos y actualiza.
"""
from decimal import Decimal, InvalidOperation


class ConfigQrInvalida(Exception):
    """Datos de configuración inválidos. `mensaje` es apto para mostrar al
    usuario (los MISMOS textos que respondía la web); `codigo` lo usa la API
    para su JSON de error uniforme (la web solo usa el mensaje)."""

    def __init__(self, mensaje, codigo):
        super().__init__(mensaje)
        self.mensaje = mensaje
        self.codigo = codigo


def _coord(valor):
    """Convierte el valor recibido a Decimal SIN truncar; None si falta o no es
    numérico. (Movida desde checkpoints/views.py; mismo comportamiento.)"""
    if valor is None or str(valor).strip() == "":
        return None
    try:
        return Decimal(str(valor).strip())
    except (InvalidOperation, ValueError):
        return None


# Valores que la web considera "verdadero" para el checkbox no_validar. Se
# compara sobre str(valor) para aceptar también el booleano JSON de la API
# (True -> "True") sin alterar el comportamiento con strings del form web.
_VERDADEROS = ("1", "true", "on", "True")


def aplicar_configuracion_qr(cp, *, lat, lng, tolerancia_mts, no_validar):
    """Valida y aplica la configuración por escaneo al punto (reglas de la web):

    - lat/lng OBLIGATORIOS y numéricos (coordenadas reales del teléfono, sin
      truncar); si faltan o no son numéricos -> ConfigQrInvalida (GPS requerido).
    - Rango geográfico: lat [-90, 90], lng [-180, 180].
    - tolerancia_mts: entero >= 0; si no viene válida, CONSERVA la actual.
    - no_validar "verdadero" -> validar_posicion = False.

    NO cambia qr_token, nombre ni tipo. Devuelve el punto ya guardado."""
    lat_dec = _coord(lat)
    lng_dec = _coord(lng)
    if lat_dec is None or lng_dec is None:
        raise ConfigQrInvalida(
            "Falta la ubicación: debes permitir el GPS para configurar.", "gps_requerido"
        )
    # Rango geográfico válido (evita coordenadas imposibles del GPS).
    if not (-90 <= lat_dec <= 90):
        raise ConfigQrInvalida("Latitud fuera de rango (-90 a 90).", "lat_fuera_de_rango")
    if not (-180 <= lng_dec <= 180):
        raise ConfigQrInvalida("Longitud fuera de rango (-180 a 180).", "lng_fuera_de_rango")

    # Tolerancia entera >= 0; si no viene válida, conserva la actual.
    try:
        tolerancia = int(tolerancia_mts)
        if tolerancia < 0:
            raise ValueError
    except (TypeError, ValueError):
        tolerancia = cp.tolerancia_mts

    cp.lat = lat_dec                  # coordenadas reales del teléfono, sin truncar
    cp.lng = lng_dec
    cp.validar_posicion = str(no_validar) not in _VERDADEROS
    cp.tolerancia_mts = tolerancia
    cp.save(update_fields=["lat", "lng", "validar_posicion", "tolerancia_mts"])
    return cp
