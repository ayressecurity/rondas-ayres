"""
Tests de AISLAMIENTO del rol 'cliente' (seguridad crítica):

  1. Helpers permisos.cliente_de / es_cliente (lectura del claim del token).
  2. ForzarClienteMiddleware: amarra la sesión al cliente del token en CADA
     request, corrige intentos de forzar otro cliente, y deja SIN contexto (nunca
     "ver todos") a un cliente sin cliente_id resoluble. No toca a los demás roles.
"""
from types import SimpleNamespace

import jwt
from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from apps.cuentas import permisos
from apps.espejo.models import Cliente, Instalacion


def _token(**claims):
    """JWT sin firmar-relevante (el proyecto lo lee con verify_signature=False)."""
    return jwt.encode(claims, "x", algorithm="HS256")


def _req(**claims):
    """Request mínimo con el token en sesión (lo que leen los helpers de permisos)."""
    return SimpleNamespace(session={"oidc_access_token": _token(**claims)})


class HelpersPermisosClienteTests(TestCase):
    # ---- cliente_de ----
    def test_cliente_id_string_a_int(self):
        req = _req(cliente_id="400", realm_access={"roles": ["cliente"]})
        self.assertEqual(permisos.cliente_de(req), 400)

    def test_claim_ausente_devuelve_none(self):
        # super_admin/sspp/cenapoc/guardias no llevan el atributo.
        req = _req(realm_access={"roles": ["sspp"]})
        self.assertIsNone(permisos.cliente_de(req))

    def test_claim_no_numerico_devuelve_none(self):
        self.assertIsNone(permisos.cliente_de(_req(cliente_id="abc")))
        self.assertIsNone(permisos.cliente_de(_req(cliente_id="4.0")))
        self.assertIsNone(permisos.cliente_de(_req(cliente_id="")))

    def test_sin_token_devuelve_none(self):
        self.assertIsNone(permisos.cliente_de(SimpleNamespace(session={})))

    # ---- es_cliente ----
    def test_es_cliente_true_false(self):
        self.assertTrue(permisos.es_cliente(_req(realm_access={"roles": ["cliente"]})))
        self.assertFalse(permisos.es_cliente(_req(realm_access={"roles": ["sspp"]})))


@override_settings(STORAGES={
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
})
class ForzarClienteMiddlewareTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create(username="cli")
        self.client = Client()
        self.client.force_login(self.user)
        Cliente.objects.create(id=400, razon_social="Muni Las Condes", rut="400-9")

    def _rol(self, roles, cliente_id=None):
        claims = {"realm_access": {"roles": roles}}
        if cliente_id is not None:
            claims["cliente_id"] = str(cliente_id)
        s = self.client.session
        s["oidc_access_token"] = _token(**claims)
        s.save()

    def test_fuerza_cliente_del_token(self):
        self._rol(["cliente"], cliente_id=400)
        self.client.get(reverse("comun:dashboard"))
        self.assertEqual(self.client.session["cliente_id"], 400)
        self.assertEqual(self.client.session["cliente_nombre"], "Muni Las Condes")

    def test_reforza_aunque_intenten_otro_cliente(self):
        self._rol(["cliente"], cliente_id=400)
        s = self.client.session
        s["cliente_id"] = 999          # intento de forzar otro cliente
        s.save()
        self.client.get(reverse("comun:dashboard"))
        self.assertEqual(self.client.session["cliente_id"], 400)   # corregido

    def test_sin_cliente_resoluble_queda_sin_contexto(self):
        # Rol cliente pero SIN claim cliente_id -> se limpia todo (nunca "ver todos").
        self._rol(["cliente"], cliente_id=None)
        s = self.client.session
        s["cliente_id"] = 400          # resto de una sesión previa
        s["instalacion_id"] = 40
        s.save()
        self.client.get(reverse("comun:dashboard"))
        self.assertNotIn("cliente_id", self.client.session)
        self.assertNotIn("instalacion_id", self.client.session)

    def test_cliente_borrado_no_da_contexto(self):
        # cliente_id apunta a un cliente que no existe/está borrado -> sin contexto.
        self._rol(["cliente"], cliente_id=777)
        self.client.get(reverse("comun:dashboard"))
        self.assertNotIn("cliente_id", self.client.session)

    def test_sanea_instalacion_de_otro_cliente(self):
        # Defensa transversal: instalacion de otro cliente en sesion -> descartada,
        # aunque el cliente (400) esté bien fijado. Cubre checkpoints/control_vehicular/etc.
        Instalacion.objects.create(id=40, codigo="AYR-0040", cliente_id=400, nombre="Propia")
        Instalacion.objects.create(id=50, codigo="AYR-0050", cliente_id=500, nombre="Ajena")
        Cliente.objects.create(id=500, razon_social="Otro", rut="500-9")
        self._rol(["cliente"], cliente_id=400)
        s = self.client.session
        s["cliente_id"] = 400
        s["instalacion_id"] = 50   # de otra empresa
        s.save()
        self.client.get(reverse("comun:dashboard"))
        self.assertEqual(self.client.session["cliente_id"], 400)
        self.assertNotIn("instalacion_id", self.client.session)   # saneada

    def test_conserva_instalacion_propia(self):
        Instalacion.objects.create(id=40, codigo="AYR-0040", cliente_id=400, nombre="Propia")
        self._rol(["cliente"], cliente_id=400)
        s = self.client.session
        s["cliente_id"] = 400
        s["instalacion_id"] = 40   # de su propia empresa
        s.save()
        self.client.get(reverse("comun:dashboard"))
        self.assertEqual(self.client.session["instalacion_id"], 40)   # intacta

    def test_super_admin_no_es_forzado(self):
        # super_admin (aunque llevara cliente_id) elige libremente: no se fuerza.
        self._rol(["super_admin", "cliente"], cliente_id=400)
        s = self.client.session
        s["cliente_id"] = 999
        s.save()
        self.client.get(reverse("comun:dashboard"))
        self.assertEqual(self.client.session["cliente_id"], 999)   # intacto

    def test_sspp_no_es_forzado(self):
        self._rol(["sspp"])
        s = self.client.session
        s["cliente_id"] = 123
        s.save()
        self.client.get(reverse("comun:dashboard"))
        self.assertEqual(self.client.session["cliente_id"], 123)   # intacto
