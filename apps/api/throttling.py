"""
Throttling de la API. SOLO se usa en el enrolamiento de dispositivos (público):
se aplica POR-VISTA con @throttle_classes, NO globalmente. El resto de endpoints
no llevan throttle (siguen protegidos solo por el portero JWT).
"""
from rest_framework.throttling import AnonRateThrottle


class EnrollThrottle(AnonRateThrottle):
    """1 intento de enrolamiento cada 30 min por IP (anti fuerza bruta del secreto).

    La tasa vive en settings (DEFAULT_THROTTLE_RATES['enroll'] = '1/30min').
    DRF nativo no expresa "cada 30 minutos", así que extendemos parse_rate para
    aceptar el sufijo 'min'; el resto de formatos estándar se delegan a DRF.
    """
    scope = "enroll"

    def parse_rate(self, rate):
        if rate is None:
            return (None, None)
        num, _, periodo = rate.partition("/")
        if periodo.endswith("min"):
            minutos = int(periodo[:-3] or 1)
            return (int(num), minutos * 60)
        return super().parse_rate(rate)
