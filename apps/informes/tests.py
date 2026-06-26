"""
Tests de la resolución de filtros de fecha de los informes (apps/informes/base.py).

Foco: el DEFAULT de fecha es HOY (Santiago) SOLO cuando no hay ningún valor de
fecha. Si el usuario aplica cualquier filtro (rango/semana, mes, año, día), ese
filtro manda y "hoy" NO interviene. "hoy" es un default automático, no un control
(no se inyecta en `valores`).
"""
from django.test import RequestFactory, TestCase
from django.utils import timezone

from apps.informes.base import _rango_y_label


class RangoFechaDefaultTests(TestCase):
    def setUp(self):
        self.rf = RequestFactory()

    def test_sin_parametros_default_hoy(self):
        request = self.rf.get("/informes/rondas/")  # primera carga, sin querystring
        rango, etiqueta, valores = _rango_y_label(request)

        hoy = timezone.localtime(timezone.now()).date()
        self.assertIsNotNone(rango)
        self.assertEqual(etiqueta, f"Día {hoy.isoformat()}")
        # 'hoy' es default automático, NO un valor de control (no se repinta).
        self.assertEqual(valores["dia"], "")

    def test_formulario_vacio_tambien_default_hoy(self):
        # Enviar el form con todo vacío = no hay filtro activo -> hoy.
        request = self.rf.get(
            "/informes/rondas/",
            {"dia": "", "fini": "", "ffin": "", "mes": "", "anio": ""},
        )
        rango, etiqueta, _ = _rango_y_label(request)

        hoy = timezone.localtime(timezone.now()).date()
        self.assertIsNotNone(rango)
        self.assertEqual(etiqueta, f"Día {hoy.isoformat()}")

    def test_rango_semana_se_respeta_sin_forzar_hoy(self):
        request = self.rf.get(
            "/informes/rondas/", {"fini": "2026-06-01", "ffin": "2026-06-07"}
        )
        rango, etiqueta, valores = _rango_y_label(request)

        self.assertEqual(etiqueta, "2026-06-01 a 2026-06-07")
        # El rango cubre los 7 días [01, 08) — NO se forzó "hoy".
        inicio, fin = rango
        self.assertEqual((fin - inicio).days, 7)
        self.assertEqual(valores["fini"], "2026-06-01")
        self.assertEqual(valores["ffin"], "2026-06-07")

    def test_mes_anio_se_respeta_sin_forzar_hoy(self):
        request = self.rf.get("/informes/rondas/", {"mes": "3", "anio": "2026"})
        rango, etiqueta, _ = _rango_y_label(request)

        self.assertEqual(etiqueta, "Marzo 2026")
        self.assertIsNotNone(rango)

    def test_anio_solo_se_respeta(self):
        request = self.rf.get("/informes/rondas/", {"anio": "2026"})
        _rango, etiqueta, _valores = _rango_y_label(request)
        self.assertEqual(etiqueta, "Año 2026")

    def test_dia_explicito_se_respeta(self):
        request = self.rf.get("/informes/rondas/", {"dia": "2026-01-15"})
        rango, etiqueta, valores = _rango_y_label(request)

        self.assertIsNotNone(rango)
        self.assertEqual(etiqueta, "Día 2026-01-15")
        self.assertEqual(valores["dia"], "2026-01-15")
