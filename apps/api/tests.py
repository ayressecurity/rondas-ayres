"""
Tests de la API. NO dependen de Keycloak real: generamos un par de llaves RSA de
test, firmamos tokens y parcheamos `obtener_llave` para devolver la llave pública
de test (así no hay red ni JWKS real).

- PorteroApiTests: el portero del Prompt A (firma, exp, JIT, normalización).
- LecturasApiTests: los 3 endpoints de lectura del Prompt B + aislamiento por guardia.
"""
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch
from uuid import UUID

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.checkpoints.models import PuntoControl
from apps.rondas.models import (
    DestinoNotificacion,
    EstadoGenerico,
    Notificacion,
    Ronda,
    RondaGuardia,
    RondaSecuencia,
)

URL_ME = "/api/me"
SUB = "11111111-2222-3333-4444-555555555555"  # con guiones, como en el token


def _claims(sub=SUB, **extra):
    """Claims base de un token válido (iss correcto, exp en el futuro)."""
    ahora = datetime.now(tz=timezone.utc)
    base = {
        "sub": sub,
        "iss": settings.OIDC_OP_ISSUER,
        "iat": ahora,
        "exp": ahora + timedelta(hours=1),
        # Único por sub (Keycloak garantiza username único; el alta JIT lo usa).
        "preferred_username": f"guardia.{sub.split('-')[0]}",
        "email": "guardia@ayressecurity.cl",
        "given_name": "Guardia",
        "family_name": "Test",
        "realm_access": {"roles": ["guardia", "default-roles-ayres"]},
    }
    base.update(extra)
    return base


class _JwtMixin:
    """Infra compartida: par de llaves RSA de test, parche del JWKS y helpers."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Par de llaves RSA (una sola vez; generar es costoso).
        cls.priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.pub = cls.priv.public_key()
        # Segunda llave para simular firma inválida.
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

    def _get(self, url, sub=SUB):
        """GET autenticado como el guardia `sub`."""
        return self.client.get(url, HTTP_AUTHORIZATION=f"Bearer {self._token(_claims(sub=sub))}")


class PorteroApiTests(_JwtMixin, TestCase):
    def _auth(self, claims, key=None):
        return self.client.get(URL_ME, HTTP_AUTHORIZATION=f"Bearer {self._token(claims, key)}")

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
        self.assertEqual(user.username, f"guardia.{SUB.split('-')[0]}")

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


class LecturasApiTests(_JwtMixin, TestCase):
    # Dos guardias distintos (sub CON guiones, como en el token).
    SUB_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    SUB_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"

    def setUp(self):
        super().setUp()
        # Punto de control activo de la instalación 10.
        self.cp = PuntoControl.objects.create(
            instalacion_id=10, nombre="Porton Norte",
            lat="-33.40000000000000000", lng="-70.56000000000000000",
            tolerancia_mts=30, validar_posicion=True,
            qr_token="11111111-1111-1111-1111-111111111111", activo=True,
        )
        # Ronda del guardia A (con 1 punto en secuencia) y ronda del guardia B.
        self.ronda_a = Ronda.objects.create(
            cliente_id=1, instalacion_id=10, nombre="Ronda A", fecha_inicio=date(2026, 1, 1),
        )
        self.ronda_b = Ronda.objects.create(
            cliente_id=1, instalacion_id=10, nombre="Ronda B", fecha_inicio=date(2026, 1, 1),
        )
        RondaSecuencia.objects.create(ronda=self.ronda_a, punto_control=self.cp, orden=1)
        RondaGuardia.objects.create(ronda=self.ronda_a, guardia_keycloak_id=self.SUB_A)
        RondaGuardia.objects.create(ronda=self.ronda_b, guardia_keycloak_id=self.SUB_B)

    # ---- by-qr ----
    def test_by_qr_valido_200(self):
        resp = self._get(f"/api/checkpoints/by-qr/{self.cp.qr_token}", sub=self.SUB_A)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["id"], self.cp.id)
        self.assertEqual(data["instalacion_id"], 10)
        self.assertEqual(data["nombre"], "Porton Norte")

    def test_by_qr_inexistente_404(self):
        resp = self._get("/api/checkpoints/by-qr/no-existe-este-token", sub=self.SUB_A)
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["error"]["codigo"], "no_encontrado")

    def test_by_qr_inactivo_404(self):
        self.cp.activo = False
        self.cp.save(update_fields=["activo"])
        resp = self._get(f"/api/checkpoints/by-qr/{self.cp.qr_token}", sub=self.SUB_A)
        self.assertEqual(resp.status_code, 404)

    def test_by_qr_sin_token_401(self):
        resp = self.client.get(f"/api/checkpoints/by-qr/{self.cp.qr_token}")
        self.assertEqual(resp.status_code, 401)

    # ---- rondas?mias ----
    def test_rondas_mias_solo_del_guardia(self):
        resp = self._get("/api/rondas?mias", sub=self.SUB_A)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["nombre"], "Ronda A")
        # Trae la secuencia para armar la ruta.
        self.assertEqual(data[0]["secuencia"], [{"punto_control_id": self.cp.id, "orden": 1}])

    def test_rondas_aislamiento_entre_guardias(self):
        """El guardia A NUNCA ve la ronda del guardia B y viceversa."""
        a = self._get("/api/rondas?mias", sub=self.SUB_A).json()
        b = self._get("/api/rondas?mias", sub=self.SUB_B).json()
        self.assertEqual([r["nombre"] for r in a], ["Ronda A"])
        self.assertEqual([r["nombre"] for r in b], ["Ronda B"])

    def test_rondas_sin_token_401(self):
        self.assertEqual(self.client.get("/api/rondas?mias").status_code, 401)

    # ---- notificaciones?mias ----
    def test_notificaciones_mias_solo_suyas(self):
        # Para A: una 'guardia' (lo nombra) + una 'todos' de su ronda.
        Notificacion.objects.create(
            ronda=self.ronda_a, destino_tipo=DestinoNotificacion.GUARDIA,
            destino_ref=self.SUB_A, anticipacion_min=10, mensaje="Para A directo",
        )
        Notificacion.objects.create(
            ronda=self.ronda_a, destino_tipo=DestinoNotificacion.TODOS,
            destino_ref=None, anticipacion_min=5, mensaje="Para todos de Ronda A",
        )
        # Para B: una 'guardia' que NO debe ver A.
        Notificacion.objects.create(
            ronda=self.ronda_b, destino_tipo=DestinoNotificacion.GUARDIA,
            destino_ref=self.SUB_B, anticipacion_min=10, mensaje="Para B directo",
        )
        # 'grupo' NO se soporta aún: no debe aparecer para nadie.
        Notificacion.objects.create(
            ronda=self.ronda_a, destino_tipo=DestinoNotificacion.GRUPO,
            destino_ref="99", anticipacion_min=10, mensaje="Grupo (ignorado)",
        )

        data = self._get("/api/notificaciones?mias", sub=self.SUB_A).json()
        mensajes = {n["mensaje"] for n in data}
        self.assertEqual(mensajes, {"Para A directo", "Para todos de Ronda A"})

    def test_notificaciones_aislamiento_entre_guardias(self):
        Notificacion.objects.create(
            ronda=self.ronda_b, destino_tipo=DestinoNotificacion.GUARDIA,
            destino_ref=self.SUB_B, anticipacion_min=10, mensaje="Solo para B",
        )
        data = self._get("/api/notificaciones?mias", sub=self.SUB_A).json()
        self.assertEqual(data, [])  # A no ve nada de B

    def test_notificaciones_sin_token_401(self):
        self.assertEqual(self.client.get("/api/notificaciones?mias").status_code, 401)
