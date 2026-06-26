"""
Test de aislamiento cliente -> instalación (QA #4): al seleccionar, la instalación
debe pertenecer al cliente en sesión; si no, se rechaza y no se fija.
"""
from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from apps.espejo.models import Instalacion


class SeleccionarInstalacionTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create(username="u1")
        self.client = Client()
        self.client.force_login(self.user)
        s = self.client.session
        s["cliente_id"] = 1  # cliente seleccionado
        s.save()

    def test_instalacion_de_otro_cliente_rechazada(self):
        ajena = Instalacion.objects.create(id=500, codigo="AYR-0500", cliente_id=2, nombre="Ajena")
        resp = self.client.get(reverse("instalaciones:seleccionar", args=[ajena.id]))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("instalaciones:index"))
        self.assertNotIn("instalacion_id", self.client.session)  # NO se fijó

    def test_instalacion_del_cliente_se_fija(self):
        propia = Instalacion.objects.create(id=501, codigo="AYR-0501", cliente_id=1, nombre="Propia")
        resp = self.client.get(reverse("instalaciones:seleccionar", args=[propia.id]))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(self.client.session.get("instalacion_id"), 501)
