"""
Tests de la resolución de filtros de fecha de los informes (apps/informes/base.py).

Solo dos filtros: AÑO y RANGO (fini/ffin). El día actual es un DEFAULT AUTOMÁTICO
e INVISIBLE: se aplica solo cuando no hay año ni rango. Si el usuario usa año o
rango, ese filtro manda y "hoy" NO interviene.
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
        # 'hoy' es default automático: NO hay control de día (no se repinta).
        self.assertNotIn("dia", valores)

    def test_formulario_vacio_tambien_default_hoy(self):
        # Año y rango vacíos = no hay filtro activo -> hoy.
        request = self.rf.get("/informes/rondas/", {"anio": "", "fini": "", "ffin": ""})
        rango, etiqueta, _ = _rango_y_label(request)

        hoy = timezone.localtime(timezone.now()).date()
        self.assertIsNotNone(rango)
        self.assertEqual(etiqueta, f"Día {hoy.isoformat()}")

    def test_rango_se_respeta_sin_forzar_hoy(self):
        # Rango que abarca de un mes a otro (calendario).
        request = self.rf.get(
            "/informes/rondas/", {"fini": "2026-03-01", "ffin": "2026-05-31"}
        )
        rango, etiqueta, valores = _rango_y_label(request)

        self.assertEqual(etiqueta, "2026-03-01 a 2026-05-31")
        inicio, fin = rango
        # [2026-03-01, 2026-06-01) -> 92 días (marzo 31 + abril 30 + mayo 31).
        self.assertEqual((fin - inicio).days, 92)
        self.assertEqual(valores["fini"], "2026-03-01")
        self.assertEqual(valores["ffin"], "2026-05-31")

    def test_anio_se_respeta_sin_forzar_hoy(self):
        request = self.rf.get("/informes/rondas/", {"anio": "2026"})
        rango, etiqueta, valores = _rango_y_label(request)

        self.assertIsNotNone(rango)
        self.assertEqual(etiqueta, "Año 2026")
        self.assertEqual(valores["anio"], "2026")

    def test_anio_tiene_precedencia_sobre_rango(self):
        # Si por algún motivo vienen ambos, manda el año (precedencia definida).
        request = self.rf.get(
            "/informes/rondas/",
            {"anio": "2026", "fini": "2026-03-01", "ffin": "2026-05-31"},
        )
        _rango, etiqueta, _valores = _rango_y_label(request)
        self.assertEqual(etiqueta, "Año 2026")
