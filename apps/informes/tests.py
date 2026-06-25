"""
Tests de la resolución de filtros de fecha de los informes (apps/informes/base.py).

Foco: el DEFAULT de fecha (Parte 2). En primera carga (GET sin parámetros de
fecha) el rango debe ser el día de HOY (Santiago); si el formulario se envía con
todo vacío, se respeta "Todos".
"""
from django.test import RequestFactory, TestCase
from django.utils import timezone

from apps.informes.base import _rango_y_label


class RangoFechaDefaultTests(TestCase):
    def setUp(self):
        self.rf = RequestFactory()

    def test_primera_carga_sin_filtros_defaultea_a_hoy(self):
        request = self.rf.get("/informes/rondas/")  # sin querystring
        rango, etiqueta, valores = _rango_y_label(request)

        hoy = timezone.localtime(timezone.now()).date()
        self.assertIsNotNone(rango)
        self.assertEqual(etiqueta, f"Día {hoy.isoformat()}")
        # La UI debe mostrar el día de hoy seleccionado.
        self.assertEqual(valores["dia"], hoy.isoformat())

    def test_formulario_enviado_todo_vacio_es_todos(self):
        # El form siempre manda las claves aunque vacías -> el usuario quiere Todos.
        request = self.rf.get("/informes/rondas/", {"dia": "", "fini": "", "ffin": "", "mes": "", "anio": ""})
        rango, etiqueta, _valores = _rango_y_label(request)

        self.assertIsNone(rango)
        self.assertEqual(etiqueta, "Todos")

    def test_dia_explicito_se_respeta(self):
        request = self.rf.get("/informes/rondas/", {"dia": "2026-01-15"})
        rango, etiqueta, valores = _rango_y_label(request)

        self.assertIsNotNone(rango)
        self.assertEqual(etiqueta, "Día 2026-01-15")
        self.assertEqual(valores["dia"], "2026-01-15")
