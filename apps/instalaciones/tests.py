"""
Test de aislamiento cliente -> instalación (QA #4): al seleccionar, la instalación
debe pertenecer al cliente en sesión; si no, se rechaza y no se fija.
"""
import jwt
from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from apps.espejo.models import Cliente, Instalacion


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


class RolClienteInstalacionesTests(TestCase):
    """3.3a: el rol 'cliente', amarrado a su cliente por el middleware, no puede
    seleccionar una instalación de otra empresa (aunque fuerce el id por URL)."""

    def setUp(self):
        self.user = get_user_model().objects.create(username="cli")
        self.client = Client()
        self.client.force_login(self.user)
        Cliente.objects.create(id=400, razon_social="Muni Las Condes", rut="400-9")
        Cliente.objects.create(id=500, razon_social="Otro Cliente", rut="500-9")
        self.propia = Instalacion.objects.create(id=40, codigo="AYR-0040", cliente_id=400, nombre="Propia")
        self.ajena = Instalacion.objects.create(id=50, codigo="AYR-0050", cliente_id=500, nombre="Ajena")
        token = jwt.encode(
            {"realm_access": {"roles": ["cliente"]}, "cliente_id": "400"},
            "x", algorithm="HS256",
        )
        s = self.client.session
        s["oidc_access_token"] = token
        s.save()

    def test_no_puede_seleccionar_instalacion_ajena(self):
        # El middleware fuerza cliente_id=400; seleccionar rechaza la de cliente 500.
        resp = self.client.get(reverse("instalaciones:seleccionar", args=[self.ajena.id]))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("instalaciones:index"))
        self.assertNotIn("instalacion_id", self.client.session)

    def test_puede_seleccionar_su_instalacion(self):
        resp = self.client.get(reverse("instalaciones:seleccionar", args=[self.propia.id]))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(self.client.session.get("instalacion_id"), 40)
