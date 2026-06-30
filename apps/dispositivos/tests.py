"""
Tests del módulo Dispositivos (enrolamiento por QR).

- GuardiaSyncTests: el test-guardia que impide que 'qr'/'codigo' entren al sync.
- EnrollApiTests: el endpoint público POST /api/dispositivos/enroll (Fase 3).
- DispositivosWebTests: el módulo web (SSPP/super_admin): generar/rotar QR, lista,
  revocar/reactivar, control de acceso.

NO dependemos de Keycloak real: el acceso web se simula poniendo un access token
(sin firmar; permisos lo lee con verify_signature=False) en la sesión.
"""
import hashlib
from uuid import UUID

import jwt
from django.core.cache import cache
from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from apps.dispositivos.models import Dispositivo
from apps.dispositivos.utils import hash_token
from apps.espejo.models import Instalacion
from apps.espejo.sync import INSTALACION_FIELDS

SUB = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
SECRETO = "secreto-de-enrolamiento-de-prueba"


def _crear_instalacion(qr=None):
    return Instalacion.objects.create(
        id=10, codigo="AYR-0001", cliente_id=1, nombre="Puesto 1", qr=qr,
    )


# ---------------------------------------------------------------------------
# Test-guardia del sync (Fase 1.3)
# ---------------------------------------------------------------------------
class GuardiaSyncTests(TestCase):
    def test_campos_propios_no_estan_en_la_lista_del_sync(self):
        # Si alguien agrega 'qr' o 'codigo' a INSTALACION_FIELDS, el sync los
        # pisaría con None en cada sincronización. Este test lo impide.
        self.assertNotIn("qr", INSTALACION_FIELDS)
        self.assertNotIn("codigo", INSTALACION_FIELDS)


# ---------------------------------------------------------------------------
# API pública de enrolamiento (Fase 3)
# ---------------------------------------------------------------------------
class EnrollApiTests(TestCase):
    URL = "/api/dispositivos/enroll"

    def setUp(self):
        # El throttle usa la caché del proceso: la limpiamos para aislar cada test
        # (el límite es 1/30min por IP y todos los tests comparten 127.0.0.1).
        cache.clear()
        self.api = APIClient()
        self.inst = _crear_instalacion(qr=SECRETO)

    def test_enroll_valido_201_show_once_y_solo_hash(self):
        resp = self.api.post(self.URL, {"s": SECRETO, "nombre": "Tel 1"}, format="json")
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        token = data["device_token"]
        self.assertTrue(token)                               # token en plano, una vez
        self.assertEqual(data["instalacion"]["id"], self.inst.id)

        disp = Dispositivo.objects.get(id=data["dispositivo_id"])
        self.assertEqual(disp.instalacion_id, self.inst.id)  # derivada del secreto
        self.assertEqual(disp.nombre, "Tel 1")
        self.assertTrue(disp.activo)
        # En BD solo el hash; el token en plano NO se persiste.
        self.assertEqual(disp.token_hash, hashlib.sha256(token.encode()).hexdigest())
        self.assertEqual(disp.token_hash, hash_token(token))
        self.assertFalse(Dispositivo.objects.filter(token_hash=token).exists())

    def test_enroll_secreto_invalido_400(self):
        resp = self.api.post(self.URL, {"s": "no-existe"}, format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"]["mensaje"], "Código de configuración inválido.")
        self.assertEqual(Dispositivo.objects.count(), 0)

    def test_enroll_sin_secreto_400(self):
        resp = self.api.post(self.URL, {}, format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(Dispositivo.objects.count(), 0)

    def test_reenroll_crea_dos_filas_distintas(self):
        r1 = self.api.post(self.URL, {"s": SECRETO}, format="json")
        cache.clear()  # saltamos el throttle: aquí probamos el re-enrolamiento, no el límite
        r2 = self.api.post(self.URL, {"s": SECRETO}, format="json")
        self.assertEqual(r1.status_code, 201)
        self.assertEqual(r2.status_code, 201)
        self.assertEqual(Dispositivo.objects.count(), 2)
        self.assertNotEqual(r1.json()["device_token"], r2.json()["device_token"])

    def test_throttle_segundo_intento_429(self):
        self.assertEqual(self.api.post(self.URL, {"s": SECRETO}, format="json").status_code, 201)
        # Segundo intento desde la misma IP dentro de la ventana -> bloqueado.
        self.assertEqual(self.api.post(self.URL, {"s": SECRETO}, format="json").status_code, 429)

    def test_instalacion_sale_del_secreto_no_del_body(self):
        # Aunque el body intente forzar otra instalación, se ignora.
        resp = self.api.post(
            self.URL, {"s": SECRETO, "instalacion_id": 99999}, format="json",
        )
        self.assertEqual(resp.status_code, 201)
        disp = Dispositivo.objects.get(id=resp.json()["dispositivo_id"])
        self.assertEqual(disp.instalacion_id, self.inst.id)


# ---------------------------------------------------------------------------
# Web (SSPP / super_admin)
# ---------------------------------------------------------------------------
@override_settings(STORAGES={
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
})
class DispositivosWebTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create(username="sspp", keycloak_id=UUID(SUB))
        self.inst = _crear_instalacion()
        self.client = Client()
        self.client.force_login(self.user)
        self._sesion_con_rol(["super_admin"])

    def _sesion_con_rol(self, roles):
        """Fija cliente/instalación en sesión y un access token con esos roles."""
        token = jwt.encode({"realm_access": {"roles": roles}}, "x", algorithm="HS256")
        s = self.client.session
        s["cliente_id"] = 1
        s["instalacion_id"] = self.inst.id
        s["instalacion_nombre"] = self.inst.nombre
        s["oidc_access_token"] = token
        s.save()

    # ---- acceso ----
    def test_super_admin_entra(self):
        resp = self.client.get(reverse("dispositivos:index"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "QR de enrolamiento")

    def test_sspp_entra(self):
        self._sesion_con_rol(["sspp"])
        self.assertEqual(self.client.get(reverse("dispositivos:index")).status_code, 200)

    def test_rol_no_autorizado_redirige(self):
        self._sesion_con_rol(["guardia"])
        resp = self.client.get(reverse("dispositivos:index"))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("comun:dashboard"))

    # ---- generar / rotar ----
    def test_generar_crea_secreto(self):
        resp = self.client.post(reverse("dispositivos:generar"))
        self.assertRedirects(resp, reverse("dispositivos:index"))
        self.inst.refresh_from_db()
        self.assertTrue(self.inst.qr)

    def test_rotar_cambia_secreto_y_no_desactiva_dispositivos(self):
        self.inst.qr = "secreto-viejo"
        self.inst.save(update_fields=["qr"])
        disp = Dispositivo.objects.create(
            instalacion_id=self.inst.id, token_hash="a" * 64, activo=True,
        )
        resp = self.client.post(reverse("dispositivos:rotar"))
        self.assertRedirects(resp, reverse("dispositivos:index"))
        self.inst.refresh_from_db()
        disp.refresh_from_db()
        self.assertNotEqual(self.inst.qr, "secreto-viejo")  # secreto cambiado
        self.assertTrue(disp.activo)                        # el dispositivo sigue activo

    # ---- revocar / reactivar ----
    def test_revocar_y_reactivar(self):
        disp = Dispositivo.objects.create(
            instalacion_id=self.inst.id, token_hash="b" * 64, activo=True,
        )
        self.client.post(reverse("dispositivos:revocar", args=[disp.id]))
        disp.refresh_from_db()
        self.assertFalse(disp.activo)

        self.client.post(reverse("dispositivos:reactivar", args=[disp.id]))
        disp.refresh_from_db()
        self.assertTrue(disp.activo)

    def test_revocar_dispositivo_de_otra_instalacion_404(self):
        # Aislamiento: no se puede tocar un dispositivo de otra instalación.
        ajeno = Dispositivo.objects.create(instalacion_id=999, token_hash="c" * 64)
        resp = self.client.post(reverse("dispositivos:revocar", args=[ajeno.id]))
        self.assertEqual(resp.status_code, 404)

    def test_lista_vacia_mensaje(self):
        resp = self.client.get(reverse("dispositivos:index"))
        self.assertContains(resp, "Aún no hay dispositivos enrolados en esta instalación.")
