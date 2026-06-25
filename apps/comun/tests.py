"""
Tests del service compartido de rondas (apps/comun/services/rondas.py).

Verifican el comportamiento que ANTES vivía en el escáner: decisión de
tipo_evento, bloqueo de re-escaneo y reuso de la ejecución en curso. Además,
el CONTRATO DE IDENTIDAD: el guardia_keycloak_id se escribe TAL CUAL (con guiones).
"""
from datetime import date, time

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from apps.checkpoints.models import PuntoControl
from apps.comun.services.rondas import (
    iniciar_o_reusar_ejecucion,
    registrar_escaneo,
)
from apps.escaner.models import RondaEjecucion
from apps.novedades.models import LibroNovedades
from apps.rondas.models import Ronda, RondaSecuencia

# Guardia de prueba: sub CON guiones (como llega del token / del UUID del user).
GUARDIA = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
# Coordenadas del punto (Las Condes aprox).
LAT, LNG = -33.40000000000000000, -70.56000000000000000


class ServiceRondasTests(TestCase):
    def setUp(self):
        # Catálogo de eventos real (arribo, arribo_sin_geo, arribo_invalido, ...).
        call_command("seed_tipos_evento")
        self.cp = PuntoControl.objects.create(
            instalacion_id=10, nombre="Porton Norte",
            lat=str(LAT), lng=str(LNG),
            tolerancia_mts=30, validar_posicion=True,
            qr_token="11111111-1111-1111-1111-111111111111", activo=True,
        )

    def _ultimo_evento(self):
        return LibroNovedades.objects.order_by("-id").first()

    # ---- decisión de tipo_evento ----
    def test_arribo_dentro_de_tolerancia(self):
        res = registrar_escaneo(
            instalacion_id=10, guardia_keycloak_id=GUARDIA,
            qr_token=self.cp.qr_token, lat=LAT, lng=LNG, texto=None,
            ahora=timezone.now(),
        )
        self.assertEqual(res["resultado"], "ok")
        self.assertTrue(res["dentro_geocerca"])
        ev = self._ultimo_evento()
        self.assertEqual(ev.tipo_evento.codigo, "arribo")
        self.assertTrue(ev.dentro_geocerca)
        self.assertEqual(ev.instalacion_id, 10)  # del propio punto

    def test_arribo_invalido_fuera_de_tolerancia(self):
        # ~1.1 km al norte: fuera de los 30 m de tolerancia.
        res = registrar_escaneo(
            instalacion_id=10, guardia_keycloak_id=GUARDIA,
            qr_token=self.cp.qr_token, lat=LAT + 0.01, lng=LNG, texto=None,
            ahora=timezone.now(),
        )
        self.assertEqual(res["resultado"], "ok")
        self.assertFalse(res["dentro_geocerca"])
        self.assertEqual(self._ultimo_evento().tipo_evento.codigo, "arribo_invalido")

    def test_arribo_sin_geo_cuando_no_valida_posicion(self):
        self.cp.validar_posicion = False
        self.cp.save(update_fields=["validar_posicion"])
        res = registrar_escaneo(
            instalacion_id=10, guardia_keycloak_id=GUARDIA,
            qr_token=self.cp.qr_token, lat=LAT, lng=LNG, texto=None,
            ahora=timezone.now(),
        )
        self.assertEqual(res["resultado"], "ok")
        self.assertIsNone(res["dentro_geocerca"])
        self.assertEqual(self._ultimo_evento().tipo_evento.codigo, "arribo_sin_geo")

    def test_codigo_no_existe_registra_y_avisa(self):
        res = registrar_escaneo(
            instalacion_id=10, guardia_keycloak_id=GUARDIA,
            qr_token="no-calza", lat=LAT, lng=LNG, texto=None,
            ahora=timezone.now(),
        )
        self.assertEqual(res["resultado"], "codigo_no_existe")
        ev = self._ultimo_evento()
        self.assertEqual(ev.tipo_evento.codigo, "codigo_no_existe")
        self.assertEqual(ev.instalacion_id, 0)  # punto desconocido

    # ---- contrato de identidad ----
    def test_guardia_se_escribe_con_guiones_tal_cual(self):
        registrar_escaneo(
            instalacion_id=10, guardia_keycloak_id=GUARDIA,
            qr_token=self.cp.qr_token, lat=LAT, lng=LNG, texto=None,
            ahora=timezone.now(),
        )
        self.assertEqual(self._ultimo_evento().guardia_keycloak_id, GUARDIA)

    # ---- iniciar: reuso de ejecución ----
    def _ronda_activa_ahora(self):
        ronda = Ronda.objects.create(
            cliente_id=1, instalacion_id=10, nombre="Ronda Día",
            fecha_inicio=date(2026, 1, 1),
            hora_inicio=time(0, 0, 0), hora_fin=time(23, 59, 59),
        )
        RondaSecuencia.objects.create(ronda=ronda, punto_control=self.cp, orden=1)
        return ronda

    def test_iniciar_reusa_la_ejecucion_en_curso(self):
        self._ronda_activa_ahora()
        ej1, _v1, _e1 = iniciar_o_reusar_ejecucion(
            instalacion_id=10, guardia_keycloak_id=GUARDIA, ahora=timezone.now(),
        )
        ej2, _v2, _e2 = iniciar_o_reusar_ejecucion(
            instalacion_id=10, guardia_keycloak_id=GUARDIA, ahora=timezone.now(),
        )
        self.assertEqual(ej1.id, ej2.id)  # reusa, no crea otra
        self.assertEqual(RondaEjecucion.objects.count(), 1)

    # ---- bloqueo de re-escaneo ----
    def test_bloqueo_no_duplica_mismo_punto_en_la_ventana(self):
        ronda = self._ronda_activa_ahora()
        # Segundo punto para que la ronda NO se complete tras 1 escaneo
        # (si se completara, la ejecución dejaría de estar "en curso").
        cp2 = PuntoControl.objects.create(
            instalacion_id=10, nombre="Porton Sur", lat=str(LAT), lng=str(LNG),
            tolerancia_mts=30, validar_posicion=True,
            qr_token="22222222-2222-2222-2222-222222222222", activo=True,
        )
        RondaSecuencia.objects.create(ronda=ronda, punto_control=cp2, orden=2)

        iniciar_o_reusar_ejecucion(
            instalacion_id=10, guardia_keycloak_id=GUARDIA, ahora=timezone.now(),
        )
        r1 = registrar_escaneo(
            instalacion_id=10, guardia_keycloak_id=GUARDIA,
            qr_token=self.cp.qr_token, lat=LAT, lng=LNG, texto=None,
            ahora=timezone.now(),
        )
        r2 = registrar_escaneo(
            instalacion_id=10, guardia_keycloak_id=GUARDIA,
            qr_token=self.cp.qr_token, lat=LAT, lng=LNG, texto=None,
            ahora=timezone.now(),
        )
        self.assertEqual(r1["resultado"], "ok")
        self.assertEqual(r2["resultado"], "ya_escaneado")
        # Solo UN arribo para ese punto en la ventana.
        arribos = LibroNovedades.objects.filter(
            punto_control=self.cp, tipo_evento__codigo="arribo",
        ).count()
        self.assertEqual(arribos, 1)
