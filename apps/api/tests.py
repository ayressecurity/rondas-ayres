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

    # ---- idempotencia: reintento no duplica ----
    def test_reintento_mismo_punto_no_duplica(self):
        cp2 = PuntoControl.objects.create(
            instalacion_id=10, nombre="Porton Sur", lat=str(self.LAT), lng=str(self.LNG),
            tolerancia_mts=30, validar_posicion=True,
            qr_token="22222222-2222-2222-2222-222222222222", activo=True,
        )
        RondaSecuencia.objects.create(ronda=self.ronda, punto_control=cp2, orden=2)

        r1 = self._post(self.URL, self._body(), sub=self.GUARDIA)
        r2 = self._post(self.URL, self._body(), sub=self.GUARDIA)
        self.assertEqual(r1.status_code, 201)
        self.assertEqual(r2.status_code, 200)
        self.assertTrue(r2.json()["ya_registrado"])
        # Un único arribo para ese punto.
        self.assertEqual(
            LibroNovedades.objects.filter(punto_control=self.cp, tipo_evento__codigo="arribo").count(),
            1,
        )

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
