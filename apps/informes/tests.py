"""
Tests de la resolución de filtros de fecha de los informes (apps/informes/base.py).

Solo dos filtros: AÑO y RANGO (fini/ffin). El día actual es un DEFAULT AUTOMÁTICO
e INVISIBLE: se aplica solo cuando no hay año ni rango. Si el usuario usa año o
rango, ese filtro manda y "hoy" NO interviene.
"""
from django.contrib.auth import get_user_model
from django.test import Client, RequestFactory, TestCase
from django.utils import timezone

from apps.informes.base import _rango_y_label
from apps.novedades.models import CategoriaEvento, LibroNovedades, TipoEvento


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


class ExportExcelSaneoTests(TestCase):
    """El export no debe romper con caracteres de control en el texto (QA #1)."""

    def setUp(self):
        self.user = get_user_model().objects.create(username="u")
        self.client = Client()
        self.client.force_login(self.user)
        s = self.client.session
        s["cliente_id"] = 1
        s["instalacion_id"] = 10
        s["instalacion_nombre"] = "Inst"
        s.save()
        tipo = TipoEvento.objects.create(codigo="arribo", nombre="Arribo", categoria=CategoriaEvento.RONDA)
        ahora = timezone.now()  # cae en el default "hoy" del informe
        LibroNovedades.objects.create(
            instalacion_id=10, guardia_keycloak_id="x", tipo_evento=tipo,
            timestamp_evento=ahora, timestamp_servidor=ahora, estado="ok",
            texto="texto\x07con\x00control",  # chars ilegales para openpyxl
        )

    def test_export_rondas_no_rompe_con_char_de_control(self):
        resp = self.client.get("/informes/rondas/excel/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("spreadsheetml", resp["Content-Type"])
