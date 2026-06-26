"""
Tests de checkpoints: validación de rango geográfico y de la foto en el form, y
que configurar_qr NO devuelva 500 si falta la instalación en sesión (QA #2, #3, #6).
"""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings

from apps.checkpoints.forms import PuntoControlForm


class PuntoControlFormTests(TestCase):
    def _data(self, **over):
        d = {"nombre": "Punto", "lat": "-33.40000", "lng": "-70.56000", "tolerancia_mts": "30"}
        d.update(over)
        return d

    def test_form_valido(self):
        self.assertTrue(PuntoControlForm(data=self._data()).is_valid())

    def test_lat_fuera_de_rango(self):
        form = PuntoControlForm(data=self._data(lat="200"))
        self.assertFalse(form.is_valid())
        self.assertIn("lat", form.errors)

    def test_lng_fuera_de_rango(self):
        form = PuntoControlForm(data=self._data(lng="-200"))
        self.assertFalse(form.is_valid())
        self.assertIn("lng", form.errors)

    def test_foto_tipo_no_permitido(self):
        form = PuntoControlForm(
            data=self._data(),
            files={"foto": SimpleUploadedFile("virus.exe", b"MZ", content_type="application/octet-stream")},
        )
        self.assertFalse(form.is_valid())
        self.assertIn("foto", form.errors)

    @override_settings(MEDIA_MAX_FOTO_MB=0)
    def test_foto_excede_tamano(self):
        form = PuntoControlForm(
            data=self._data(),
            files={"foto": SimpleUploadedFile("f.jpg", b"123", content_type="image/jpeg")},
        )
        self.assertFalse(form.is_valid())
        self.assertIn("foto", form.errors)


class ConfigurarQrSinInstalacionTests(TestCase):
    """Sin instalación en sesión, los endpoints JSON deben dar 400 claro, no 500."""

    def setUp(self):
        self.user = get_user_model().objects.create(username="sa")
        self.client = Client()
        self.client.force_login(self.user)

    @patch("apps.checkpoints.views.permisos.es_super_admin", return_value=True)
    def test_buscar_sin_instalacion_400(self, _m):
        resp = self.client.post("/checkpoints/configurar-qr/buscar/", {"qr_token": "x"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Selecciona una instalación", resp.json()["error"])

    @patch("apps.checkpoints.views.permisos.es_super_admin", return_value=True)
    def test_guardar_sin_instalacion_400(self, _m):
        resp = self.client.post(
            "/checkpoints/configurar-qr/guardar/",
            {"qr_token": "x", "lat": "-33.4", "lng": "-70.5"},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Selecciona una instalación", resp.json()["error"])
