"""
Tests de checkpoints: validación de rango geográfico y de la foto en el form, y
que configurar_qr NO devuelva 500 si falta la instalación en sesión (QA #2, #3, #6).
"""
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings

from apps.checkpoints.forms import PuntoControlForm
from apps.checkpoints.models import PuntoControl


class FotoUrlTests(TestCase):
    """foto_url resuelve la URL pública (vía MEDIA) o '' si no hay foto (QA fix)."""

    def _cp(self, foto_path=None):
        return PuntoControl(
            instalacion_id=10, nombre="P", lat="-33.4", lng="-70.5",
            tolerancia_mts=30, qr_token="x", foto_path=foto_path,
        )

    def test_con_foto_devuelve_url_publica(self):
        cp = self._cp("checkpoints/abc_foto.png")
        # Debe empezar por MEDIA_URL y terminar con la ruta del archivo.
        self.assertTrue(cp.foto_url.startswith(settings.MEDIA_URL))
        self.assertTrue(cp.foto_url.endswith("checkpoints/abc_foto.png"))

    def test_sin_foto_devuelve_vacio(self):
        self.assertEqual(self._cp(None).foto_url, "")
        self.assertEqual(self._cp("").foto_url, "")


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


@patch("apps.checkpoints.views.permisos.es_super_admin", return_value=True)
class ConfigurarQrGuardarWebTests(TestCase):
    """La web de "Configurar QR" NO cambió tras extraer la lógica al service
    compartido: mismos requests, mismas respuestas ({"ok"/"error"}, 200/400/404)."""

    QR = "77777777-7777-7777-7777-777777777777"
    URL = "/checkpoints/configurar-qr/guardar/"

    def setUp(self):
        self.user = get_user_model().objects.create(username="sa")
        self.client = Client()
        self.client.force_login(self.user)
        s = self.client.session
        s["cliente_id"] = 1
        s["instalacion_id"] = 10
        s.save()
        self.cp = PuntoControl.objects.create(
            instalacion_id=10, nombre="Porton Web",
            lat="-33.4", lng="-70.5", tolerancia_mts=30, validar_posicion=True,
            qr_token=self.QR, activo=True,
        )

    def test_guardar_ok_misma_respuesta(self, _m):
        resp = self.client.post(self.URL, {
            "qr_token": self.QR, "lat": "-33.41", "lng": "-70.57",
            "tolerancia_mts": "50", "no_validar": "0",
        })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"ok": True, "nombre": "Porton Web"})  # respuesta intacta
        self.cp.refresh_from_db()
        self.assertEqual(str(self.cp.lat), "-33.41000000000000000")
        self.assertEqual(self.cp.tolerancia_mts, 50)
        self.assertTrue(self.cp.validar_posicion)

    def test_guardar_sin_gps_400_mismo_mensaje(self, _m):
        resp = self.client.post(self.URL, {"qr_token": self.QR})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Falta la ubicación", resp.json()["error"])

    def test_guardar_lat_fuera_de_rango_400(self, _m):
        resp = self.client.post(self.URL, {"qr_token": self.QR, "lat": "200", "lng": "-70.5"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Latitud fuera de rango", resp.json()["error"])

    def test_guardar_qr_de_otra_instalacion_404(self, _m):
        resp = self.client.post(self.URL, {
            "qr_token": "no-es-de-aqui", "lat": "-33.4", "lng": "-70.5",
        })
        self.assertEqual(resp.status_code, 404)
        self.assertIn("no pertenece a esta instalación", resp.json()["error"])
