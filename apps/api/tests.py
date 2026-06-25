"""
Tests del portero de la API. NO dependen de Keycloak real: generamos un par de
llaves RSA de test, firmamos tokens y parcheamos `obtener_llave` para devolver
la llave pública de test (así no hay red ni JWKS real).

Cubre: sin token -> 401, token válido -> 200 (+sub/roles), token vencido -> 401,
firma inválida -> 401, alta JIT (is_staff/is_superuser False) y que la
normalización del keycloak_id casa un sub con guiones contra una fila sin guiones.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import UUID

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

URL_ME = "/api/me"
SUB = "11111111-2222-3333-4444-555555555555"  # con guiones, como en el token


def _claims(**extra):
    """Claims base de un token válido (iss correcto, exp en el futuro)."""
    ahora = datetime.now(tz=timezone.utc)
    base = {
        "sub": SUB,
        "iss": settings.OIDC_OP_ISSUER,
        "iat": ahora,
        "exp": ahora + timedelta(hours=1),
        "preferred_username": "guardia.test",
        "email": "guardia@ayressecurity.cl",
        "given_name": "Guardia",
        "family_name": "Test",
        "realm_access": {"roles": ["guardia", "default-roles-ayres"]},
    }
    base.update(extra)
    return base


class PorteroApiTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Par de llaves RSA de test (una sola vez; generar es costoso).
        cls.priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.pub = cls.priv.public_key()
        # Una segunda llave para simular firma inválida.
        cls.priv_intruso = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def setUp(self):
        self.client = APIClient()
        # El portero pide la llave por kid: devolvemos siempre la pública de test.
        parche = patch("apps.api.authentication.obtener_llave", return_value=self.pub)
        parche.start()
        self.addCleanup(parche.stop)

    def _token(self, claims, key=None):
        return jwt.encode(claims, key or self.priv, algorithm="RS256",
                          headers={"kid": "test"})

    def _auth(self, claims, key=None):
        return self.client.get(URL_ME, HTTP_AUTHORIZATION=f"Bearer {self._token(claims, key)}")

    # ---- casos ----
    def test_sin_token_401(self):
        resp = self.client.get(URL_ME)
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()["error"]["codigo"], "no_autenticado")

    def test_token_valido_200(self):
        resp = self._auth(_claims())
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["sub"], SUB)             # con guiones
        self.assertIn("guardia", data["roles"])
        self.assertTrue(data["creado_jit"])            # primera vez: se crea JIT

    def test_token_vencido_401(self):
        ahora = datetime.now(tz=timezone.utc)
        resp = self._auth(_claims(iat=ahora - timedelta(hours=2),
                                  exp=ahora - timedelta(hours=1)))
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()["error"]["codigo"], "no_autenticado")

    def test_firma_invalida_401(self):
        # Firmado con la llave del intruso; el portero valida con la pública real.
        resp = self._auth(_claims(), key=self.priv_intruso)
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()["error"]["codigo"], "no_autenticado")

    def test_alta_jit_sin_privilegios(self):
        Usuario = get_user_model()
        self.assertFalse(Usuario.objects.filter(keycloak_id=UUID(SUB)).exists())
        resp = self._auth(_claims())
        self.assertEqual(resp.status_code, 200)
        user = Usuario.objects.get(keycloak_id=UUID(SUB))
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertEqual(user.username, "guardia.test")

    def test_normalizacion_casa_sub_con_guiones(self):
        """Una fila ya existente (keycloak_id sin guiones) debe casar con el sub
        con guiones del token: NO se crea otra fila, creado_jit=False."""
        Usuario = get_user_model()
        Usuario.objects.create(username="ya.existe", keycloak_id=UUID(SUB))
        antes = Usuario.objects.count()

        resp = self._auth(_claims())
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["creado_jit"])
        self.assertEqual(Usuario.objects.count(), antes)  # no se duplicó
