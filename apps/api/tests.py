"""
Tests de la API. NO dependen de Keycloak real: generamos un par de llaves RSA de
test, firmamos tokens y parcheamos `obtener_llave` para devolver la llave pública
de test (así no hay red ni JWKS real).

- PorteroApiTests: el portero del Prompt A (firma, exp, JIT, normalización).
- LecturasApiTests: los 3 endpoints de lectura del Prompt B + aislamiento por guardia.
- EventosApiTests: POST /api/eventos del Prompt E (registro vía service compartido).
"""
import shutil
import tempfile
from datetime import date, datetime, time as dtime, timedelta, timezone
from unittest.mock import patch
from uuid import UUID

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone as dj_tz
from rest_framework.test import APIClient

from apps.checkpoints.models import PuntoControl
from apps.dispositivos.models import Dispositivo
from apps.dispositivos.utils import hash_token
from apps.espejo.models import Cliente, Instalacion
from apps.novedades.models import (
    CategoriaEvento,
    LibroNovedades,
    LibroNovedadesMedia,
    TipoEvento,
)
from apps.rondas.models import (
    DestinoNotificacion,
    EstadoGenerico,
    Notificacion,
    Programacion,
    ProgramacionHorario,
    Ronda,
    RondaGuardia,
    RondaSecuencia,
)

# MEDIA en carpeta temporal: los tests de subida no tocan el media real.
_MEDIA_TMP = tempfile.mkdtemp()

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

    def _post(self, url, body, sub=SUB):
        """POST JSON autenticado como el guardia `sub`."""
        return self.client.post(
            url, body, format="json",
            HTTP_AUTHORIZATION=f"Bearer {self._token(_claims(sub=sub))}",
        )


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
        # Trae la secuencia para armar la ruta (con nombre del punto, opción B).
        self.assertEqual(
            data[0]["secuencia"],
            [{"orden": 1, "punto_control_id": self.cp.id, "nombre": self.cp.nombre}],
        )

    def test_rondas_aislamiento_entre_guardias(self):
        """El guardia A NUNCA ve la ronda del guardia B y viceversa."""
        a = self._get("/api/rondas?mias", sub=self.SUB_A).json()
        b = self._get("/api/rondas?mias", sub=self.SUB_B).json()
        self.assertEqual([r["nombre"] for r in a], ["Ronda A"])
        self.assertEqual([r["nombre"] for r in b], ["Ronda B"])

    def test_rondas_sin_token_401(self):
        self.assertEqual(self.client.get("/api/rondas?mias").status_code, 401)

    # ---- rondas?mias con DISPOSITIVO (Fase 4) ----
    def _device_token(self, instalacion_id=10, token="dev-rondas", activo=True):
        Dispositivo.objects.create(
            instalacion_id=instalacion_id, token_hash=hash_token(token), activo=activo,
        )
        return token

    def test_rondas_con_device_devuelve_rondas_de_la_instalacion(self):
        # Con X-Device-Token: TODAS las rondas activas de la instalación del
        # dispositivo (10 -> A y B), no solo las "asignadas" al guardia.
        token = self._device_token(instalacion_id=10)
        resp = self.client.get(
            "/api/rondas?mias",
            HTTP_AUTHORIZATION=f"Bearer {self._token(_claims(sub=self.SUB_A))}",
            HTTP_X_DEVICE_TOKEN=token,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(sorted(r["nombre"] for r in resp.json()), ["Ronda A", "Ronda B"])

    def test_rondas_sin_device_mantiene_fallback_por_guardia(self):
        # Sin device: comportamiento de SIEMPRE (solo la ronda del guardia A).
        data = self._get("/api/rondas?mias", sub=self.SUB_A).json()
        self.assertEqual([r["nombre"] for r in data], ["Ronda A"])

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


class EventosApiTests(_JwtMixin, TestCase):
    """POST /api/eventos — registro de marcas vía el service compartido."""
    URL = "/api/eventos"
    GUARDIA = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"  # sub del token, CON guiones
    LAT, LNG = -33.40000000000000000, -70.56000000000000000

    def setUp(self):
        super().setUp()
        call_command("seed_tipos_evento")  # catálogo real de tipo_evento
        self.cp = PuntoControl.objects.create(
            instalacion_id=10, nombre="Porton Norte",
            lat=str(self.LAT), lng=str(self.LNG),
            tolerancia_mts=30, validar_posicion=True,
            qr_token="11111111-1111-1111-1111-111111111111", activo=True,
        )
        # Ronda activa AHORA (rango que cubre todo el día) con el punto en secuencia.
        self.ronda = Ronda.objects.create(
            cliente_id=1, instalacion_id=10, nombre="Ronda Día",
            fecha_inicio=date(2026, 1, 1),
            hora_inicio=dtime(0, 0, 0), hora_fin=dtime(23, 59, 59),
        )
        RondaSecuencia.objects.create(ronda=self.ronda, punto_control=self.cp, orden=1)

    def _body(self, **extra):
        body = {"qr_token": self.cp.qr_token, "lat": self.LAT, "lng": self.LNG}
        body.update(extra)
        return body

    # ---- evento nuevo ----
    def test_evento_nuevo_201_y_fila_con_guardia_con_guiones(self):
        resp = self._post(self.URL, self._body(), sub=self.GUARDIA)
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertEqual(data["tipo_evento"], "arribo")
        self.assertTrue(data["dentro_geocerca"])

        ev = LibroNovedades.objects.get(id=data["id"])
        # Identidad del TOKEN, CON guiones, tal cual (igual que la web).
        self.assertEqual(ev.guardia_keycloak_id, self.GUARDIA)
        # Instalación DERIVADA del punto (opción a).
        self.assertEqual(ev.instalacion_id, self.cp.instalacion_id)
        self.assertEqual(ev.punto_control_id, self.cp.id)
        self.assertEqual(ev.tipo_evento.codigo, "arribo")

    # ---- API: no cruza entre instalaciones aunque exista otra "Ronda Día" ----
    def test_api_no_cruza_entre_instalaciones(self):
        from datetime import timedelta

        from apps.comun.services.rondas import iniciar_o_reusar_ejecucion
        from apps.escaner.models import RondaEjecucion

        # Otra instalación (11) con su propia "Ronda Día" activa y su punto.
        cp_b = PuntoControl.objects.create(
            instalacion_id=11, nombre="Porton B", lat=str(self.LAT), lng=str(self.LNG),
            tolerancia_mts=30, validar_posicion=True,
            qr_token="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", activo=True,
        )
        ronda_b = Ronda.objects.create(
            cliente_id=1, instalacion_id=11, nombre="Ronda Día",
            fecha_inicio=date(2026, 1, 1), hora_inicio=dtime(0, 0, 0), hora_fin=dtime(23, 59, 59),
        )
        RondaSecuencia.objects.create(ronda=ronda_b, punto_control=cp_b, orden=1)

        # El mismo guardia tiene una ejecución en curso en B, MÁS reciente.
        ej_b, _v, _e = iniciar_o_reusar_ejecucion(
            instalacion_id=11, guardia_keycloak_id=self.GUARDIA, ahora=dj_tz.now(),
        )
        RondaEjecucion.objects.filter(id=ej_b.id).update(
            iniciada_en=dj_tz.now() + timedelta(minutes=5)
        )

        # POST con el QR de un punto de A (10) -> se registra contra la ronda de A.
        resp = self._post(self.URL, self._body(), sub=self.GUARDIA)
        self.assertEqual(resp.status_code, 201)
        ev = LibroNovedades.objects.get(id=resp.json()["id"])
        self.assertEqual(ev.ronda_id, self.ronda.id)      # ronda de A...
        self.assertNotEqual(ev.ronda_id, ronda_b.id)      # ...nunca la de B

    # ---- re-escaneo: registra fila nueva; progreso cuenta únicos ----
    def test_reescaneo_mismo_punto_registra_progreso_unico(self):
        cp2 = PuntoControl.objects.create(
            instalacion_id=10, nombre="Porton Sur", lat=str(self.LAT), lng=str(self.LNG),
            tolerancia_mts=30, validar_posicion=True,
            qr_token="22222222-2222-2222-2222-222222222222", activo=True,
        )
        RondaSecuencia.objects.create(ronda=self.ronda, punto_control=cp2, orden=2)

        r1 = self._post(self.URL, self._body(), sub=self.GUARDIA)
        r2 = self._post(self.URL, self._body(), sub=self.GUARDIA)
        # Re-escaneo permitido: ambas responden 201 (arribo).
        self.assertEqual(r1.status_code, 201)
        self.assertEqual(r2.status_code, 201)
        self.assertEqual(r2.json()["tipo_evento"], "arribo")
        # DOS filas de arribo para ese punto...
        self.assertEqual(
            LibroNovedades.objects.filter(punto_control=self.cp, tipo_evento__codigo="arribo").count(),
            2,
        )
        # ...pero el progreso lo cuenta UNA vez (puntos únicos): 1 de 2.
        self.assertEqual(r2.json()["progreso"]["escaneados"], 1)
        self.assertEqual(r2.json()["progreso"]["total"], 2)

    # ---- GPS fuera de tolerancia: NO se rechaza ----
    def test_gps_fuera_de_tolerancia_201_arribo_invalido(self):
        resp = self._post(self.URL, self._body(lat=self.LAT + 0.01), sub=self.GUARDIA)
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.json()["tipo_evento"], "arribo_invalido")
        self.assertFalse(resp.json()["dentro_geocerca"])

    def test_punto_sin_validar_posicion_201_arribo_sin_geo(self):
        self.cp.validar_posicion = False
        self.cp.save(update_fields=["validar_posicion"])
        resp = self._post(self.URL, self._body(), sub=self.GUARDIA)
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.json()["tipo_evento"], "arribo_sin_geo")

    # ---- identidad: el body NO puede suplantar al guardia ----
    def test_body_con_guardia_se_ignora(self):
        resp = self._post(
            self.URL,
            self._body(guardia_keycloak_id="99999999-9999-9999-9999-999999999999", sub="otro"),
            sub=self.GUARDIA,
        )
        self.assertEqual(resp.status_code, 201)
        ev = LibroNovedades.objects.get(id=resp.json()["id"])
        self.assertEqual(ev.guardia_keycloak_id, self.GUARDIA)  # el del TOKEN, no el del body

    # ---- validaciones ----
    def test_sin_qr_token_400(self):
        resp = self._post(self.URL, {"lat": self.LAT, "lng": self.LNG}, sub=self.GUARDIA)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"]["codigo"], "solicitud_invalida")

    def test_sin_token_401(self):
        self.assertEqual(self.client.post(self.URL, self._body(), format="json").status_code, 401)

    # ---- web vs api: mismo service -> registro equivalente ----
    def test_web_y_api_producen_registro_equivalente(self):
        """Llamar al service como la WEB y registrar por la API con los mismos
        datos produce un libro_novedades equivalente (mismo punto/tipo/instalación
        y guardia CON guiones)."""
        from apps.comun.services.rondas import registrar_escaneo

        # Vía "web": llamada directa al service (instalacion de la sesión = la del punto).
        web = registrar_escaneo(
            instalacion_id=self.cp.instalacion_id, guardia_keycloak_id=self.GUARDIA,
            qr_token=self.cp.qr_token, lat=self.LAT, lng=self.LNG, texto=None,
            ahora=dj_tz.now(),
        )
        ev_web = LibroNovedades.objects.get(id=web["libro_id"])

        # Vía API: otro guardia (para no chocar con el bloqueo del primero).
        otro = "ffffffff-ffff-ffff-ffff-ffffffffffff"
        resp = self._post(self.URL, self._body(), sub=otro)
        ev_api = LibroNovedades.objects.get(id=resp.json()["id"])

        self.assertEqual(ev_web.punto_control_id, ev_api.punto_control_id)
        self.assertEqual(ev_web.tipo_evento_id, ev_api.tipo_evento_id)
        self.assertEqual(ev_web.instalacion_id, ev_api.instalacion_id)
        self.assertEqual(ev_web.guardia_keycloak_id, self.GUARDIA)
        self.assertEqual(ev_api.guardia_keycloak_id, otro)  # cada uno con SU sub, con guiones

    # ---- marca fuera de ventana de ronda (decisión #8) ----
    def test_sin_ronda_activa_no_registra(self):
        # Punto de una instalación SIN ronda activa -> 400, sin escribir nada.
        cp_sin = PuntoControl.objects.create(
            instalacion_id=20, nombre="Sin Ronda", lat=str(self.LAT), lng=str(self.LNG),
            tolerancia_mts=30, validar_posicion=True,
            qr_token="20202020-2020-2020-2020-202020202020", activo=True,
        )
        antes = LibroNovedades.objects.count()
        resp = self._post(self.URL, {"qr_token": cp_sin.qr_token, "lat": self.LAT, "lng": self.LNG}, sub=self.GUARDIA)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"]["codigo"], "solicitud_invalida")
        self.assertIn("ronda activa", resp.json()["error"]["mensaje"])
        self.assertEqual(LibroNovedades.objects.count(), antes)

    # ---- rango geográfico (decisión #2) ----
    def test_lat_fuera_de_rango_400(self):
        resp = self._post(self.URL, {"qr_token": self.cp.qr_token, "lat": 200, "lng": self.LNG}, sub=self.GUARDIA)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Latitud fuera de rango", resp.json()["error"]["mensaje"])

    def test_lng_fuera_de_rango_400(self):
        resp = self._post(self.URL, {"qr_token": self.cp.qr_token, "lat": self.LAT, "lng": 999}, sub=self.GUARDIA)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Longitud fuera de rango", resp.json()["error"]["mensaje"])

    # ---- separación de timestamps en la API (decisión #5) ----
    def test_timestamp_evento_de_terreno_distinto_de_servidor(self):
        terreno = (dj_tz.now() - timedelta(hours=2)).isoformat()
        resp = self._post(
            self.URL,
            {"qr_token": self.cp.qr_token, "lat": self.LAT, "lng": self.LNG, "timestamp_evento": terreno},
            sub=self.GUARDIA,
        )
        self.assertEqual(resp.status_code, 201)
        ev = LibroNovedades.objects.get(id=resp.json()["id"])
        # timestamp_servidor = ahora del server; timestamp_evento = terreno (pasado).
        self.assertGreater(ev.timestamp_servidor, ev.timestamp_evento)

    # ---- API: re-escaneo registra en la misma ventana y en la siguiente ----
    def test_api_reescaneo_en_misma_y_distinta_ventana(self):
        from apps.rondas.models import Programacion, ProgramacionHorario

        # Alarmas 12:00 / 14:00 en la ronda de A (instalación 10, día completo).
        prog = Programacion.objects.create(ronda=self.ronda, repite="todos_los_dias", activo=True)
        ProgramacionHorario.objects.create(programacion=prog, hora=12, minuto=0, orden=1)
        ProgramacionHorario.objects.create(programacion=prog, hora=14, minuto=0, orden=2)

        hoy = dj_tz.localtime(dj_tz.now()).date()

        def momento(hh, mm):
            return dj_tz.make_aware(datetime.combine(hoy, dtime(hh, mm)))

        # Controlamos el reloj que ve crear_evento (la ronda es de día completo,
        # así que _ronda_para_ahora —que usa la hora real— igual la encuentra).
        with patch("apps.api.views.timezone.now", return_value=momento(12, 30)):
            r1 = self._post(self.URL, self._body(), sub=self.GUARDIA)   # ventana 1
            r2 = self._post(self.URL, self._body(), sub=self.GUARDIA)   # misma ventana: re-escaneo
        with patch("apps.api.views.timezone.now", return_value=momento(14, 30)):
            r3 = self._post(self.URL, self._body(), sub=self.GUARDIA)   # ventana 2

        # Re-escaneo permitido: las TRES registran arribo (201), la ventana no bloquea.
        self.assertEqual(r1.status_code, 201)
        self.assertEqual(r2.status_code, 201)
        self.assertEqual(r3.status_code, 201)
        self.assertEqual(
            LibroNovedades.objects.filter(punto_control=self.cp, tipo_evento__codigo="arribo").count(), 3
        )

    # ---- Fase 4: identidad del DISPOSITIVO (header X-Device-Token) ----
    def _device_token(self, instalacion_id=10, token="device-1", activo=True):
        Dispositivo.objects.create(
            instalacion_id=instalacion_id, token_hash=hash_token(token), activo=activo,
        )
        return token

    def _post_device(self, body, token, sub=None):
        return self.client.post(
            self.URL, body, format="json",
            HTTP_AUTHORIZATION=f"Bearer {self._token(_claims(sub=sub or self.GUARDIA))}",
            HTTP_X_DEVICE_TOKEN=token,
        )

    def test_marca_con_device_valido_sella_dispositivo_id(self):
        token = self._device_token(instalacion_id=10)  # misma instalación que el QR
        disp = Dispositivo.objects.get(token_hash=hash_token(token))
        resp = self._post_device(self._body(), token)
        self.assertEqual(resp.status_code, 201)
        ev = LibroNovedades.objects.get(id=resp.json()["id"])
        self.assertEqual(ev.dispositivo_id, disp.id)   # sellado por el authenticator
        disp.refresh_from_db()
        self.assertIsNotNone(disp.last_seen)           # touch() registró presencia

    def test_marca_device_de_otra_instalacion_400(self):
        token = self._device_token(instalacion_id=11)  # el QR es de la instalación 10
        antes = LibroNovedades.objects.count()
        resp = self._post_device(self._body(), token)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"]["codigo"], "solicitud_invalida")
        self.assertIn("no está enrolado", resp.json()["error"]["mensaje"])
        self.assertEqual(LibroNovedades.objects.count(), antes)  # doble candado: no registra

    def test_marca_sin_device_dispositivo_id_null(self):
        # Como hoy: sin X-Device-Token, dispositivo_id queda NULL.
        resp = self._post(self.URL, self._body(), sub=self.GUARDIA)
        self.assertEqual(resp.status_code, 201)
        self.assertIsNone(LibroNovedades.objects.get(id=resp.json()["id"]).dispositivo_id)

    def test_device_token_invalido_se_ignora_sin_401(self):
        # Token que no casa con ningún dispositivo: NO 401; se trata como sin device.
        resp = self._post_device(self._body(), "token-inexistente")
        self.assertEqual(resp.status_code, 201)
        self.assertIsNone(LibroNovedades.objects.get(id=resp.json()["id"]).dispositivo_id)

    def test_device_revocado_se_ignora(self):
        token = self._device_token(instalacion_id=10, activo=False)  # revocado
        resp = self._post_device(self._body(), token)
        self.assertEqual(resp.status_code, 201)
        self.assertIsNone(LibroNovedades.objects.get(id=resp.json()["id"]).dispositivo_id)


@override_settings(MEDIA_ROOT=_MEDIA_TMP)
class MediaApiTests(_JwtMixin, TestCase):
    """POST /api/eventos/{id}/media — subida de archivos a un evento propio."""
    OWNER = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"  # dueño del evento (sub del token)
    OTRO = "ffffffff-ffff-ffff-ffff-ffffffffffff"   # otro guardia

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(_MEDIA_TMP, ignore_errors=True)  # limpia los archivos de test

    def setUp(self):
        super().setUp()
        self.tipo = TipoEvento.objects.create(
            codigo="novedad", nombre="Novedad", categoria=CategoriaEvento.NOVEDAD,
        )
        self.evento = self._evento_de(self.OWNER)

    def _evento_de(self, guardia):
        ahora = dj_tz.now()
        return LibroNovedades.objects.create(
            instalacion_id=10, guardia_keycloak_id=guardia, tipo_evento=self.tipo,
            timestamp_evento=ahora, timestamp_servidor=ahora, estado="ok",
        )

    def _url(self, evento_id):
        return f"/api/eventos/{evento_id}/media"

    def _post_media(self, evento_id, archivos, sub=None):
        return self.client.post(
            self._url(evento_id), {"archivo": archivos}, format="multipart",
            HTTP_AUTHORIZATION=f"Bearer {self._token(_claims(sub=sub or self.OWNER))}",
        )

    @staticmethod
    def _foto(nombre="foto.jpg", contenido=b"datos-jpeg"):
        return SimpleUploadedFile(nombre, contenido, content_type="image/jpeg")

    # ---- éxito ----
    def test_subir_una_foto_201(self):
        resp = self._post_media(self.evento.id, [self._foto()])
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["tipo"], "foto")

        medios = LibroNovedadesMedia.objects.filter(libro_novedades=self.evento)
        self.assertEqual(medios.count(), 1)
        media = medios.first()
        self.assertEqual(media.tipo, "foto")
        # El archivo físico existe en MEDIA y la ruta va organizada por evento.
        self.assertTrue(default_storage.exists(media.path))
        self.assertTrue(media.path.startswith(f"libro_novedades/{self.evento.id}/"))
        # NO se conserva el nombre del cliente (nombre propio uuid).
        self.assertNotIn("foto.jpg", media.path)

    def test_subir_varios_201(self):
        archivos = [
            self._foto("a.jpg"),
            self._foto("b.png", b"datos-png"),
            SimpleUploadedFile("c.mp3", b"datos-mp3", content_type="audio/mpeg"),
        ]
        resp = self._post_media(self.evento.id, archivos)
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(len(resp.json()), 3)
        self.assertEqual(LibroNovedadesMedia.objects.filter(libro_novedades=self.evento).count(), 3)
        tipos = set(LibroNovedadesMedia.objects.values_list("tipo", flat=True))
        self.assertEqual(tipos, {"foto", "audio"})

    # ---- propiedad ----
    def test_evento_de_otro_guardia_404(self):
        ajeno = self._evento_de(self.OTRO)
        resp = self._post_media(ajeno.id, [self._foto()], sub=self.OWNER)
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["error"]["codigo"], "no_encontrado")
        self.assertEqual(LibroNovedadesMedia.objects.filter(libro_novedades=ajeno).count(), 0)

    def test_evento_inexistente_404(self):
        resp = self._post_media(999999, [self._foto()])
        self.assertEqual(resp.status_code, 404)

    # ---- validaciones ----
    def test_tipo_no_permitido_400_sin_crear_nada(self):
        malo = SimpleUploadedFile("virus.exe", b"MZ...", content_type="application/octet-stream")
        resp = self._post_media(self.evento.id, [malo])
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(LibroNovedadesMedia.objects.count(), 0)

    def test_todo_o_nada_un_invalido_no_guarda_ninguno(self):
        archivos = [self._foto("ok.jpg"), SimpleUploadedFile("malo.exe", b"x")]
        resp = self._post_media(self.evento.id, archivos)
        self.assertEqual(resp.status_code, 400)
        # Ni la foto válida se guardó (todo-o-nada).
        self.assertEqual(LibroNovedadesMedia.objects.count(), 0)

    @override_settings(MEDIA_MAX_FOTO_MB=0)
    def test_tamano_excedido_400(self):
        resp = self._post_media(self.evento.id, [self._foto(contenido=b"cualquier-cosa")])
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(LibroNovedadesMedia.objects.count(), 0)

    def test_sin_archivo_400(self):
        resp = self.client.post(
            self._url(self.evento.id), {}, format="multipart",
            HTTP_AUTHORIZATION=f"Bearer {self._token(_claims(sub=self.OWNER))}",
        )
        self.assertEqual(resp.status_code, 400)

    def test_sin_token_401(self):
        resp = self.client.post(self._url(self.evento.id), {"archivo": [self._foto()]}, format="multipart")
        self.assertEqual(resp.status_code, 401)


class VentanasDeAlarmaTests(TestCase):
    """Helper del service: ventanas_de_alarma + que _ventana_alarma se preserva."""

    def _ronda(self, hi, hf, alarmas):
        ronda = Ronda.objects.create(
            cliente_id=1, instalacion_id=30, nombre="R",
            fecha_inicio=date(2026, 1, 1), hora_inicio=hi, hora_fin=hf,
        )
        prog = Programacion.objects.create(ronda=ronda, repite="todos_los_dias", activo=True)
        for orden, (h, m) in enumerate(alarmas, start=1):
            ProgramacionHorario.objects.create(programacion=prog, hora=h, minuto=m, orden=orden)
        return ronda

    def _aware(self, dia, hh, mm):
        return dj_tz.make_aware(datetime.combine(dia, dtime(hh, mm)))

    def test_lista_ordenada_y_ventanas(self):
        from apps.comun.services.rondas import _ventana_alarma, ventanas_de_alarma

        ronda = self._ronda(dtime(8, 0), dtime(20, 0), [(10, 0), (14, 0), (18, 0)])
        ref = self._aware(date(2026, 6, 1), 15, 0)
        ventanas = ventanas_de_alarma(ronda, ref)
        # 3 ventanas ordenadas; fin = siguiente alarma − 1s (o turno_fin la última).
        self.assertEqual(len(ventanas), 3)
        horas_inicio = [v[1].time().strftime("%H:%M") for v in ventanas]
        self.assertEqual(horas_inicio, ["10:00", "14:00", "18:00"])
        self.assertEqual(ventanas[0][2], ventanas[1][1] - timedelta(seconds=1))
        # _ventana_alarma devuelve la ACTIVA (la que contiene ref = 15:00 -> 14:00).
        activa = _ventana_alarma(ronda, ref)
        self.assertEqual(activa[0].time().strftime("%H:%M"), "14:00")

    def test_cruce_de_medianoche(self):
        from apps.comun.services.rondas import ventanas_de_alarma

        # Turno nocturno 20:00 -> 06:00, alarmas 22:00 y 02:00.
        ronda = self._ronda(dtime(20, 0), dtime(6, 0), [(22, 0), (2, 0)])
        ref = self._aware(date(2026, 6, 1), 23, 0)  # noche, mismo día
        ventanas = ventanas_de_alarma(ronda, ref)
        self.assertEqual(len(ventanas), 2)
        # 22:00 del día de inicio, 02:00 del día siguiente (orden por datetime).
        self.assertEqual(ventanas[0][1].time().strftime("%H:%M"), "22:00")
        self.assertEqual(ventanas[1][1].time().strftime("%H:%M"), "02:00")
        self.assertEqual(ventanas[1][1].date(), ventanas[0][1].date() + timedelta(days=1))

    def test_sin_alarmas_devuelve_lista_vacia(self):
        from apps.comun.services.rondas import ventanas_de_alarma

        ronda = Ronda.objects.create(
            cliente_id=1, instalacion_id=30, nombre="R", fecha_inicio=date(2026, 1, 1),
            hora_inicio=dtime(8, 0), hora_fin=dtime(20, 0),
        )
        ref = self._aware(date(2026, 6, 1), 15, 0)
        self.assertEqual(ventanas_de_alarma(ronda, ref), [])


class RondasProgramacionApiTests(_JwtMixin, TestCase):
    """GET /api/rondas?mias enriquecido: turno, programación (vueltas) y secuencia."""
    SUB = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"

    def setUp(self):
        super().setUp()
        # Instalación 30: ronda diurna 08:00-20:00 con 3 alarmas y 1 punto.
        self.cp = PuntoControl.objects.create(
            instalacion_id=30, nombre="Porton Norte",
            lat="-33.4", lng="-70.56", tolerancia_mts=30, validar_posicion=True,
            qr_token="30303030-3030-3030-3030-303030303030", activo=True,
        )
        self.ronda = Ronda.objects.create(
            cliente_id=1, instalacion_id=30, nombre="Ronda Día",
            fecha_inicio=date(2026, 1, 1), hora_inicio=dtime(8, 0), hora_fin=dtime(20, 0),
        )
        RondaSecuencia.objects.create(ronda=self.ronda, punto_control=self.cp, orden=1)
        prog = Programacion.objects.create(ronda=self.ronda, repite="todos_los_dias", activo=True)
        for orden, h in enumerate((10, 14, 18), start=1):
            ProgramacionHorario.objects.create(programacion=prog, hora=h, minuto=0, orden=orden)
        self.token = "dev-prog"
        Dispositivo.objects.create(instalacion_id=30, token_hash=hash_token(self.token), activo=True)

    def _get(self, momento):
        with patch("apps.api.views.timezone.now", return_value=momento):
            return self.client.get(
                "/api/rondas?mias",
                HTTP_AUTHORIZATION=f"Bearer {self._token(_claims(sub=self.SUB))}",
                HTTP_X_DEVICE_TOKEN=self.token,
            )

    def _momento(self, hh, mm):
        hoy = dj_tz.localtime(dj_tz.now()).date()
        return dj_tz.make_aware(datetime.combine(hoy, dtime(hh, mm)))

    def test_programacion_con_estados_y_vuelta_actual(self):
        # A las 15:00: vuelta 1 (10:00) completada, vuelta 2 (14:00) activa, vuelta 3 pendiente.
        resp = self._get(self._momento(15, 0))
        self.assertEqual(resp.status_code, 200)
        ronda = resp.json()[0]
        prog = ronda["programacion"]
        self.assertEqual(prog["repite"], "todos_los_dias")
        self.assertEqual(prog["total_vueltas"], 3)
        self.assertEqual(prog["vuelta_actual"], 2)
        self.assertEqual(
            prog["horarios"],
            [
                {"orden": 1, "hora": "10:00", "estado": "completada"},
                {"orden": 2, "hora": "14:00", "estado": "activa"},
                {"orden": 3, "hora": "18:00", "estado": "pendiente"},
            ],
        )
        # secuencia con nombre del punto.
        self.assertEqual(
            ronda["secuencia"], [{"orden": 1, "punto_control_id": self.cp.id, "nombre": "Porton Norte"}]
        )

    def test_antes_de_la_primera_alarma_vuelta_actual_null(self):
        resp = self._get(self._momento(9, 0))  # antes de las 10:00
        prog = resp.json()[0]["programacion"]
        self.assertIsNone(prog["vuelta_actual"])
        self.assertEqual([h["estado"] for h in prog["horarios"]], ["pendiente", "pendiente", "pendiente"])

    def test_ronda_sin_programacion_devuelve_null(self):
        # Otra instalación (31) con ronda SIN programación y su propio dispositivo.
        Ronda.objects.create(
            cliente_id=1, instalacion_id=31, nombre="Ronda Simple",
            fecha_inicio=date(2026, 1, 1), hora_inicio=dtime(8, 0), hora_fin=dtime(20, 0),
        )
        token = "dev-sin-prog"
        Dispositivo.objects.create(instalacion_id=31, token_hash=hash_token(token), activo=True)
        resp = self.client.get(
            "/api/rondas?mias",
            HTTP_AUTHORIZATION=f"Bearer {self._token(_claims(sub=self.SUB))}",
            HTTP_X_DEVICE_TOKEN=token,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.json()[0]["programacion"])

    def test_cruza_medianoche_en_ronda_nocturna(self):
        # Instalación 32: ronda nocturna 20:00 -> 06:00.
        Ronda.objects.create(
            cliente_id=1, instalacion_id=32, nombre="Ronda Noche",
            fecha_inicio=date(2026, 1, 1), hora_inicio=dtime(20, 0), hora_fin=dtime(6, 0),
        )
        token = "dev-noche"
        Dispositivo.objects.create(instalacion_id=32, token_hash=hash_token(token), activo=True)
        resp = self.client.get(
            "/api/rondas?mias",
            HTTP_AUTHORIZATION=f"Bearer {self._token(_claims(sub=self.SUB))}",
            HTTP_X_DEVICE_TOKEN=token,
        )
        self.assertTrue(resp.json()[0]["cruza_medianoche"])

    def test_no_n_mas_uno_queries_constantes(self):
        # Con 2 rondas programadas, SERIALIZAR no escala queries por ronda: 4 fijas
        # (ronda + secuencia + programacion + horarios). Se mide el serializer aislado
        # del HTTP/auth para que el conteo sea estable y delate cualquier N+1.
        from apps.api.serializers import RondaSerializer
        from apps.api.views import _prefetch_rondas

        r2 = Ronda.objects.create(
            cliente_id=1, instalacion_id=30, nombre="Ronda Día 2",
            fecha_inicio=date(2026, 1, 1), hora_inicio=dtime(8, 0), hora_fin=dtime(20, 0),
        )
        RondaSecuencia.objects.create(ronda=r2, punto_control=self.cp, orden=1)
        prog2 = Programacion.objects.create(ronda=r2, repite="todos_los_dias", activo=True)
        ProgramacionHorario.objects.create(programacion=prog2, hora=11, minuto=0, orden=1)

        qs = (
            Ronda.objects
            .filter(instalacion_id=30, estado=EstadoGenerico.ACTIVA)
            .prefetch_related(*_prefetch_rondas())
            .order_by("nombre")
        )
        with self.assertNumQueries(4):
            data = RondaSerializer(qs, many=True, context={"ahora": self._momento(15, 0)}).data
            self.assertEqual(len(data), 2)


@override_settings(MEDIA_ROOT=_MEDIA_TMP)
class SesionApiTests(_JwtMixin, TestCase):
    """POST /api/sesion/inicio — inicio de turno (sesion_inicio) con fotos opcionales."""
    URL = "/api/sesion/inicio"
    SUB = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

    def setUp(self):
        super().setUp()
        call_command("seed_tipos_evento")  # necesita 'sesion_inicio' en el catálogo
        self.token = "dev-sesion"
        self.disp = Dispositivo.objects.create(
            instalacion_id=40, token_hash=hash_token(self.token), activo=True,
        )

    def _foto(self, nombre="foto.jpg", contenido=b"datos-jpeg"):
        return SimpleUploadedFile(nombre, contenido, content_type="image/jpeg")

    def _post(self, fotos=None, sub=None, device=True, texto=None, lat=None, lng=None):
        cuerpo = {}
        if fotos:
            cuerpo["fotos"] = fotos
        if texto is not None:
            cuerpo["texto"] = texto
        if lat is not None:
            cuerpo["lat"] = lat
        if lng is not None:
            cuerpo["lng"] = lng
        extra = {"HTTP_AUTHORIZATION": f"Bearer {self._token(_claims(sub=sub or self.SUB))}"}
        if device:
            extra["HTTP_X_DEVICE_TOKEN"] = self.token
        return self.client.post(self.URL, cuerpo, format="multipart", **extra)

    # ---- GPS opcional ----
    def test_inicio_con_gps_201(self):
        resp = self._post(lat="-33.45000000000000000", lng="-70.66000000000000000")
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(float(resp.json()["lat"]), -33.45)
        self.assertEqual(float(resp.json()["lng"]), -70.66)
        ev = LibroNovedades.objects.get(id=resp.json()["id"])
        self.assertIsNotNone(ev.lat)
        self.assertIsNotNone(ev.lng)

    def test_inicio_sin_gps_queda_null_201(self):
        resp = self._post()  # sin lat/lng
        self.assertEqual(resp.status_code, 201)
        self.assertIsNone(resp.json()["lat"])
        self.assertIsNone(resp.json()["lng"])
        ev = LibroNovedades.objects.get(id=resp.json()["id"])
        self.assertIsNone(ev.lat)
        self.assertIsNone(ev.lng)

    def test_inicio_gps_invalido_no_rompe_201(self):
        # GPS malformado -> se trata como ausente (null), NO se rechaza.
        resp = self._post(lat="abc", lng="")
        self.assertEqual(resp.status_code, 201)
        self.assertIsNone(resp.json()["lat"])
        self.assertIsNone(resp.json()["lng"])

    def test_inicio_sin_foto_201(self):
        resp = self._post()
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertEqual(data["tipo_evento"], "sesion_inicio")
        self.assertEqual(data["instalacion_id"], 40)
        self.assertEqual(data["fotos"], [])
        ev = LibroNovedades.objects.get(id=data["id"])
        self.assertEqual(ev.tipo_evento.codigo, "sesion_inicio")
        self.assertEqual(ev.dispositivo_id, self.disp.id)     # sellado por el device
        self.assertEqual(ev.instalacion_id, 40)               # de la instalación del device
        self.assertEqual(ev.guardia_keycloak_id, self.SUB)    # del token, CON guiones
        self.assertIsNone(ev.punto_control_id)                # evento simple: sin punto/ronda
        self.assertIsNone(ev.ronda_id)

    def test_inicio_con_una_foto_201(self):
        resp = self._post(fotos=[self._foto()])
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(len(resp.json()["fotos"]), 1)
        ev_id = resp.json()["id"]
        self.assertEqual(LibroNovedadesMedia.objects.filter(libro_novedades_id=ev_id).count(), 1)

    def test_inicio_con_dos_fotos_201(self):
        resp = self._post(fotos=[self._foto("a.jpg"), self._foto("b.png", b"datos-png")])
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(len(resp.json()["fotos"]), 2)

    def test_inicio_con_tres_fotos_400(self):
        antes = LibroNovedades.objects.count()
        resp = self._post(fotos=[self._foto("a.jpg"), self._foto("b.jpg"), self._foto("c.jpg")])
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"]["codigo"], "solicitud_invalida")
        self.assertEqual(LibroNovedades.objects.count(), antes)  # nada creado

    def test_inicio_foto_invalida_400_sin_crear_evento(self):
        antes = LibroNovedades.objects.count()
        malo = SimpleUploadedFile("virus.exe", b"MZ", content_type="application/octet-stream")
        resp = self._post(fotos=[malo])
        self.assertEqual(resp.status_code, 400)
        # todo-o-nada: no se creó el evento ni media (la transacción revierte).
        self.assertEqual(LibroNovedades.objects.count(), antes)
        self.assertEqual(LibroNovedadesMedia.objects.count(), 0)

    def test_inicio_sin_device_400(self):
        resp = self._post(device=False)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"]["mensaje"], "Falta el dispositivo (X-Device-Token).")
        self.assertEqual(LibroNovedades.objects.count(), 0)

    def test_inicio_sin_token_401(self):
        resp = self.client.post(self.URL, {}, format="multipart", HTTP_X_DEVICE_TOKEN=self.token)
        self.assertEqual(resp.status_code, 401)


@override_settings(MEDIA_ROOT=_MEDIA_TMP)
class NovedadApiTests(_JwtMixin, TestCase):
    """POST /api/novedades — novedad desde el móvil: texto OBLIGATORIO, fotos opcionales."""
    URL = "/api/novedades"
    SUB = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

    def setUp(self):
        super().setUp()
        call_command("seed_tipos_evento")  # necesita 'novedad' en el catálogo
        self.token = "dev-novedad"
        self.disp = Dispositivo.objects.create(
            instalacion_id=41, token_hash=hash_token(self.token), activo=True,
        )

    def _foto(self, nombre="foto.jpg", contenido=b"datos-jpeg"):
        return SimpleUploadedFile(nombre, contenido, content_type="image/jpeg")

    def _post(self, texto="Portón forzado", fotos=None, sub=None, device=True):
        cuerpo = {}
        if texto is not None:
            cuerpo["texto"] = texto
        if fotos:
            cuerpo["fotos"] = fotos
        extra = {"HTTP_AUTHORIZATION": f"Bearer {self._token(_claims(sub=sub or self.SUB))}"}
        if device:
            extra["HTTP_X_DEVICE_TOKEN"] = self.token
        return self.client.post(self.URL, cuerpo, format="multipart", **extra)

    def test_novedad_solo_texto_201(self):
        resp = self._post()
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertEqual(data["tipo_evento"], "novedad")
        self.assertEqual(data["instalacion_id"], 41)
        self.assertEqual(data["fotos"], [])
        ev = LibroNovedades.objects.get(id=data["id"])
        self.assertEqual(ev.tipo_evento.codigo, "novedad")
        self.assertEqual(ev.instalacion_id, 41)               # del dispositivo
        self.assertEqual(ev.guardia_keycloak_id, self.SUB)    # del token, CON guiones
        self.assertEqual(ev.dispositivo_id, self.disp.id)
        self.assertEqual(ev.texto, "Portón forzado")
        self.assertIsNone(ev.punto_control_id)                # evento simple: sin punto/ronda
        self.assertIsNone(ev.ronda_id)

    def test_novedad_con_una_foto_201(self):
        resp = self._post(fotos=[self._foto()])
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(len(resp.json()["fotos"]), 1)
        self.assertEqual(
            LibroNovedadesMedia.objects.filter(libro_novedades_id=resp.json()["id"]).count(), 1
        )

    def test_novedad_con_dos_fotos_201(self):
        resp = self._post(fotos=[self._foto("a.jpg"), self._foto("b.png", b"datos-png")])
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(len(resp.json()["fotos"]), 2)

    def test_novedad_sin_texto_400(self):
        antes = LibroNovedades.objects.count()
        resp = self._post(texto="   ")  # solo espacios -> vacío
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"]["mensaje"], "La observación es obligatoria.")
        self.assertEqual(LibroNovedades.objects.count(), antes)

    def test_novedad_con_tres_fotos_400(self):
        antes = LibroNovedades.objects.count()
        resp = self._post(fotos=[self._foto("a.jpg"), self._foto("b.jpg"), self._foto("c.jpg")])
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"]["codigo"], "solicitud_invalida")
        self.assertEqual(LibroNovedades.objects.count(), antes)

    def test_novedad_foto_invalida_400_sin_crear_evento(self):
        antes = LibroNovedades.objects.count()
        malo = SimpleUploadedFile("virus.exe", b"MZ", content_type="application/octet-stream")
        resp = self._post(fotos=[malo])
        self.assertEqual(resp.status_code, 400)
        # todo-o-nada: no se creó el evento ni media.
        self.assertEqual(LibroNovedades.objects.count(), antes)
        self.assertEqual(LibroNovedadesMedia.objects.count(), 0)

    def test_novedad_sin_device_400(self):
        resp = self._post(device=False)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"]["mensaje"], "Falta el dispositivo (X-Device-Token).")
        self.assertEqual(LibroNovedades.objects.count(), 0)

    def test_novedad_sin_token_401(self):
        resp = self.client.post(
            self.URL, {"texto": "x"}, format="multipart", HTTP_X_DEVICE_TOKEN=self.token
        )
        self.assertEqual(resp.status_code, 401)


class CancelarRondaApiTests(_JwtMixin, TestCase):
    """POST /api/rondas/<id>/cancelar — cancelación con observación obligatoria."""
    SUB = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

    def setUp(self):
        super().setUp()
        call_command("seed_tipos_evento")  # incluye 'ronda_cancelada'
        self.token = "dev-cancelar"
        self.disp = Dispositivo.objects.create(
            instalacion_id=50, token_hash=hash_token(self.token), activo=True,
        )
        self.ronda = Ronda.objects.create(
            cliente_id=1, instalacion_id=50, nombre="Ronda Día", fecha_inicio=date(2026, 1, 1),
        )

    def _url(self, ronda_id):
        return f"/api/rondas/{ronda_id}/cancelar"

    def _post(self, ronda_id=None, texto="No puedo hacerla", device=True, sub=None):
        cuerpo = {}
        if texto is not None:
            cuerpo["texto"] = texto
        extra = {"HTTP_AUTHORIZATION": f"Bearer {self._token(_claims(sub=sub or self.SUB))}"}
        if device:
            extra["HTTP_X_DEVICE_TOKEN"] = self.token
        return self.client.post(self._url(ronda_id or self.ronda.id), cuerpo, format="json", **extra)

    def test_tipo_ronda_cancelada_sembrado(self):
        # Lo dejó la data migration 0003 (y el seed). Categoría 'ronda'.
        self.assertTrue(
            TipoEvento.objects.filter(codigo="ronda_cancelada", categoria="ronda").exists()
        )

    def test_cancelar_con_observacion_201(self):
        resp = self._post(texto="Corte de luz, sin acceso")
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertEqual(data["tipo_evento"], "ronda_cancelada")
        self.assertEqual(data["ronda_id"], self.ronda.id)
        self.assertEqual(data["instalacion_id"], 50)
        self.assertEqual(data["observacion"], "Corte de luz, sin acceso")
        ev = LibroNovedades.objects.get(id=data["id"])
        self.assertEqual(ev.tipo_evento.codigo, "ronda_cancelada")
        self.assertEqual(ev.ronda_id, self.ronda.id)          # constancia de QUÉ ronda
        self.assertEqual(ev.instalacion_id, 50)               # del dispositivo
        self.assertEqual(ev.guardia_keycloak_id, self.SUB)    # del token, CON guiones
        self.assertEqual(ev.dispositivo_id, self.disp.id)
        self.assertEqual(ev.texto, "Corte de luz, sin acceso")

    def test_cancelar_sin_observacion_400(self):
        antes = LibroNovedades.objects.count()
        resp = self._post(texto="   ")  # solo espacios -> vacío
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(
            resp.json()["error"]["mensaje"], "La observación es obligatoria para cancelar una ronda."
        )
        self.assertEqual(LibroNovedades.objects.count(), antes)

    def test_cancelar_ronda_de_otra_instalacion_404(self):
        ajena = Ronda.objects.create(
            cliente_id=1, instalacion_id=999, nombre="Ajena", fecha_inicio=date(2026, 1, 1),
        )
        antes = LibroNovedades.objects.count()
        resp = self._post(ronda_id=ajena.id)
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["error"]["codigo"], "no_encontrado")
        self.assertEqual(LibroNovedades.objects.count(), antes)  # no registró nada

    def test_cancelar_ronda_inexistente_404(self):
        resp = self._post(ronda_id=999999)
        self.assertEqual(resp.status_code, 404)

    def test_cancelar_sin_device_400(self):
        resp = self._post(device=False)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"]["mensaje"], "Falta el dispositivo (X-Device-Token).")

    def test_cancelar_sin_token_401(self):
        resp = self.client.post(
            self._url(self.ronda.id), {"texto": "x"}, format="json", HTTP_X_DEVICE_TOKEN=self.token
        )
        self.assertEqual(resp.status_code, 401)


class InstalacionesApiTests(_JwtMixin, TestCase):
    """GET /api/instalaciones — catálogo de instalaciones vigentes con su cliente."""
    URL = "/api/instalaciones"

    def setUp(self):
        super().setUp()
        # El usuario del token ya existe (evita el INSERT del alta JIT en el conteo).
        get_user_model().objects.create(username="g", keycloak_id=UUID(SUB))
        Cliente.objects.create(id=1, razon_social="Municipalidad X", rut="1-9")
        Cliente.objects.create(id=2, razon_social="Municipalidad Y", rut="2-7")
        # Nombres a propósito desordenados para verificar el orden asc por nombre.
        Instalacion.objects.create(id=10, codigo="AYR-0010", cliente_id=1, nombre="B Puesto")
        Instalacion.objects.create(id=11, codigo="AYR-0011", cliente_id=1, nombre="A Puesto")
        Instalacion.objects.create(id=12, codigo="AYR-0012", cliente_id=2, nombre="C Puesto")
        # Eliminada (soft delete): NO debe aparecer.
        Instalacion.objects.create(
            id=13, codigo="AYR-0013", cliente_id=1, nombre="Z Borrada", deleted_at=dj_tz.now(),
        )

    def test_lista_200_con_cliente_y_orden(self):
        resp = self._get(self.URL)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        # 3 vigentes (la borrada no está), ordenadas por nombre asc.
        self.assertEqual([i["nombre"] for i in data], ["A Puesto", "B Puesto", "C Puesto"])
        item = data[0]
        self.assertEqual(set(item.keys()), {"id", "nombre", "cliente"})
        self.assertEqual(set(item["cliente"].keys()), {"id", "nombre"})

    def test_cliente_resuelto_correctamente(self):
        data = self._get(self.URL).json()
        por_id = {i["id"]: i for i in data}
        self.assertEqual(por_id[10]["cliente"], {"id": 1, "nombre": "Municipalidad X"})
        self.assertEqual(por_id[12]["cliente"], {"id": 2, "nombre": "Municipalidad Y"})

    def test_instalacion_eliminada_no_aparece(self):
        ids = {i["id"] for i in self._get(self.URL).json()}
        self.assertNotIn(13, ids)

    def test_cliente_no_resoluble_null(self):
        Instalacion.objects.create(id=20, codigo="AYR-0020", cliente_id=999, nombre="Sin Cliente")
        por_id = {i["id"]: i for i in self._get(self.URL).json()}
        self.assertIsNone(por_id[20]["cliente"])  # cliente_id inexistente -> null

    def test_sin_token_401(self):
        self.assertEqual(self.client.get(self.URL).status_code, 401)

    def test_sin_n_mas_uno(self):
        # Más instalaciones/clientes NO deben aumentar las queries (mapa batch).
        # Fijo: 1 (usuario del token) + 1 (instalaciones) + 1 (clientes) = 3.
        with self.assertNumQueries(3):
            self._get(self.URL)
