"""
Test del escáner web para la decisión #8: escanear un punto cuando NO hay ronda
activa en este horario NO registra nada y devuelve un mensaje claro.
"""
from unittest.mock import patch
from uuid import UUID

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client, TestCase

from apps.checkpoints.models import PuntoControl
from apps.novedades.models import LibroNovedades

SUB = "dddddddd-dddd-dddd-dddd-dddddddddddd"


class EscanerSinRondaTests(TestCase):
    def setUp(self):
        call_command("seed_tipos_evento")
        self.user = get_user_model().objects.create(username="sa", keycloak_id=UUID(SUB))
        self.client = Client()
        self.client.force_login(self.user)
        s = self.client.session
        s["instalacion_id"] = 30
        s.save()
        # Punto sin NINGUNA ronda activa en su instalación.
        self.cp = PuntoControl.objects.create(
            instalacion_id=30, nombre="P", lat="-33.40000", lng="-70.56000",
            tolerancia_mts=30, validar_posicion=True,
            qr_token="30303030-3030-3030-3030-303030303030", activo=True,
        )

    @patch("apps.escaner.views.permisos.es_super_admin", return_value=True)
    def test_marca_fuera_de_ronda_no_registra(self, _m):
        resp = self.client.post(
            "/escaner/registrar/",
            {"qr_token": self.cp.qr_token, "lat": "-33.40000", "lng": "-70.56000"},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("ronda activa", resp.json()["error"])
        self.assertEqual(LibroNovedades.objects.count(), 0)  # no se escribió nada
