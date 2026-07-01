"""
Tests del service compartido de rondas (apps/comun/services/rondas.py).

Verifican el comportamiento que ANTES vivía en el escáner: decisión de
tipo_evento, bloqueo de re-escaneo y reuso de la ejecución en curso. Además,
el CONTRATO DE IDENTIDAD: el guardia_keycloak_id se escribe TAL CUAL (con guiones).
"""
from datetime import date, datetime, time, timedelta

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from apps.checkpoints.models import PuntoControl
from apps.comun.services.rondas import (
    SIN_VENTANA,
    _ventana_alarma,
    iniciar_o_reusar_ejecucion,
    registrar_escaneo,
)
from apps.escaner.models import RondaEjecucion
from apps.novedades.models import LibroNovedades
from apps.rondas.models import Programacion, ProgramacionHorario, Ronda, RondaSecuencia

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
        # Ronda ACTIVA ahora (cubre todo el día) con el punto en su secuencia.
        # Necesaria desde la decisión #8: sin ronda activa no se registra nada.
        self.ronda = Ronda.objects.create(
            cliente_id=1, instalacion_id=10, nombre="Ronda Día",
            fecha_inicio=date(2026, 1, 1),
            hora_inicio=time(0, 0, 0), hora_fin=time(23, 59, 59),
        )
        RondaSecuencia.objects.create(ronda=self.ronda, punto_control=self.cp, orden=1)

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

    # ---- validación de instalación (Parte 1) ----
    def test_punto_de_otra_instalacion_no_registra(self):
        # Punto que existe pero pertenece a OTRA instalación (la 99).
        cp_otra = PuntoControl.objects.create(
            instalacion_id=99, nombre="Punto Ajeno", lat=str(LAT), lng=str(LNG),
            tolerancia_mts=30, validar_posicion=True,
            qr_token="99999999-9999-9999-9999-999999999999", activo=True,
        )
        antes = LibroNovedades.objects.count()
        res = registrar_escaneo(
            instalacion_id=10, guardia_keycloak_id=GUARDIA,   # operando la 10
            qr_token=cp_otra.qr_token, lat=LAT, lng=LNG, texto=None,
            ahora=timezone.now(),
        )
        self.assertEqual(res["resultado"], "punto_otra_instalacion")
        self.assertEqual(res["punto_nombre"], "Punto Ajeno")
        # NO se escribió NADA en libro_novedades.
        self.assertEqual(LibroNovedades.objects.count(), antes)

    # ---- CRUCE ENTRE INSTALACIONES (el bug) ----
    def _instalacion_b_con_ronda(self):
        """Crea instalación B=11 con su propia 'Ronda Día' activa ahora y su punto.
        Mismo nombre y horario que la de A=10, pero en otra instalación."""
        cp_b = PuntoControl.objects.create(
            instalacion_id=11, nombre="Porton B", lat=str(LAT), lng=str(LNG),
            tolerancia_mts=30, validar_posicion=True,
            qr_token="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", activo=True,
        )
        ronda_b = Ronda.objects.create(
            cliente_id=1, instalacion_id=11, nombre="Ronda Día",
            fecha_inicio=date(2026, 1, 1),
            hora_inicio=time(0, 0, 0), hora_fin=time(23, 59, 59),
        )
        RondaSecuencia.objects.create(ronda=ronda_b, punto_control=cp_b, orden=1)
        return cp_b, ronda_b

    def test_no_cruza_entre_instalaciones_operando_en_A(self):
        cp_b, ronda_b = self._instalacion_b_con_ronda()

        # El MISMO guardia inicia en A (10) y luego en B (11).
        iniciar_o_reusar_ejecucion(instalacion_id=10, guardia_keycloak_id=GUARDIA, ahora=timezone.now())
        ej_b, _v, _e = iniciar_o_reusar_ejecucion(instalacion_id=11, guardia_keycloak_id=GUARDIA, ahora=timezone.now())
        # Forzamos que la ejecución de B sea la MÁS reciente: así, sin el fix,
        # _ejecucion_en_curso(guardia) devolvería la de B (el cruce que causaba el bug).
        RondaEjecucion.objects.filter(id=ej_b.id).update(
            iniciada_en=timezone.now() + timedelta(minutes=5)
        )

        # Operando en A, escanear un punto de A.
        res = registrar_escaneo(
            instalacion_id=10, guardia_keycloak_id=GUARDIA,
            qr_token=self.cp.qr_token, lat=LAT, lng=LNG, texto=None, ahora=timezone.now(),
        )
        self.assertEqual(res["resultado"], "ok")
        self.assertTrue(res["pertenece"])               # el punto SÍ pertenece a la ronda de A
        self.assertEqual(res["progreso"]["escaneados"], 1)
        ev = self._ultimo_evento()
        self.assertEqual(ev.ronda_id, self.ronda.id)    # ronda de A...
        self.assertNotEqual(ev.ronda_id, ronda_b.id)    # ...NUNCA la de B

    def test_no_cruza_entre_instalaciones_operando_en_B(self):
        cp_b, ronda_b = self._instalacion_b_con_ronda()

        iniciar_o_reusar_ejecucion(instalacion_id=11, guardia_keycloak_id=GUARDIA, ahora=timezone.now())
        ej_a, _v, _e = iniciar_o_reusar_ejecucion(instalacion_id=10, guardia_keycloak_id=GUARDIA, ahora=timezone.now())
        RondaEjecucion.objects.filter(id=ej_a.id).update(
            iniciada_en=timezone.now() + timedelta(minutes=5)  # A más reciente
        )

        # Operando en B, escanear el punto de B -> resuelve la ronda de B.
        res = registrar_escaneo(
            instalacion_id=11, guardia_keycloak_id=GUARDIA,
            qr_token=cp_b.qr_token, lat=LAT, lng=LNG, texto=None, ahora=timezone.now(),
        )
        self.assertEqual(res["resultado"], "ok")
        self.assertTrue(res["pertenece"])
        self.assertEqual(self._ultimo_evento().ronda_id, ronda_b.id)

    def test_punto_de_B_operando_en_A_es_rechazado(self):
        cp_b, _ronda_b = self._instalacion_b_con_ronda()
        antes = LibroNovedades.objects.count()
        # Operando en A (10), escaneo un QR de B (11) -> rechazo, sin escribir.
        res = registrar_escaneo(
            instalacion_id=10, guardia_keycloak_id=GUARDIA,
            qr_token=cp_b.qr_token, lat=LAT, lng=LNG, texto=None, ahora=timezone.now(),
        )
        self.assertEqual(res["resultado"], "punto_otra_instalacion")
        self.assertEqual(LibroNovedades.objects.count(), antes)

    # ---- sin ronda activa (decisión #8) ----
    def test_sin_ronda_activa_no_registra(self):
        # Punto de una instalación SIN ronda activa -> rechazo, sin escribir nada.
        cp_sin = PuntoControl.objects.create(
            instalacion_id=20, nombre="Sin Ronda", lat=str(LAT), lng=str(LNG),
            tolerancia_mts=30, validar_posicion=True,
            qr_token="20202020-2020-2020-2020-202020202020", activo=True,
        )
        antes = LibroNovedades.objects.count()
        res = registrar_escaneo(
            instalacion_id=20, guardia_keycloak_id=GUARDIA,
            qr_token=cp_sin.qr_token, lat=LAT, lng=LNG, texto=None,
            ahora=timezone.now(),
        )
        self.assertEqual(res["resultado"], "sin_ronda_activa")
        self.assertEqual(LibroNovedades.objects.count(), antes)

    # ---- contrato de identidad ----
    def test_guardia_se_escribe_con_guiones_tal_cual(self):
        registrar_escaneo(
            instalacion_id=10, guardia_keycloak_id=GUARDIA,
            qr_token=self.cp.qr_token, lat=LAT, lng=LNG, texto=None,
            ahora=timezone.now(),
        )
        self.assertEqual(self._ultimo_evento().guardia_keycloak_id, GUARDIA)

    # ---- separación de timestamps (decisión #5) ----
    def test_timestamp_servidor_real_y_evento_de_terreno(self):
        ahora = timezone.now()
        terreno = ahora - timedelta(hours=3)  # marca offline de hace 3 horas
        registrar_escaneo(
            instalacion_id=10, guardia_keycloak_id=GUARDIA,
            qr_token=self.cp.qr_token, lat=LAT, lng=LNG, texto=None,
            ahora=ahora, timestamp_evento=terreno,
        )
        ev = self._ultimo_evento()
        self.assertEqual(ev.timestamp_evento, terreno)        # terreno tal cual
        self.assertEqual(ev.timestamp_servidor, ahora)        # hora real del server

    def test_web_sin_timestamp_evento_ambos_iguales(self):
        # La web no pasa timestamp_evento -> ambos = ahora (sin cambio de conducta).
        ahora = timezone.now()
        registrar_escaneo(
            instalacion_id=10, guardia_keycloak_id=GUARDIA,
            qr_token=self.cp.qr_token, lat=LAT, lng=LNG, texto=None, ahora=ahora,
        )
        ev = self._ultimo_evento()
        self.assertEqual(ev.timestamp_evento, ev.timestamp_servidor)

    # ---- iniciar: reuso de ejecución ----
    def test_iniciar_reusa_la_ejecucion_en_curso(self):
        ej1, _v1, _e1 = iniciar_o_reusar_ejecucion(
            instalacion_id=10, guardia_keycloak_id=GUARDIA, ahora=timezone.now(),
        )
        ej2, _v2, _e2 = iniciar_o_reusar_ejecucion(
            instalacion_id=10, guardia_keycloak_id=GUARDIA, ahora=timezone.now(),
        )
        self.assertEqual(ej1.id, ej2.id)  # reusa, no crea otra
        self.assertEqual(RondaEjecucion.objects.count(), 1)

    # ---- re-escaneo: registra fila nueva, progreso cuenta únicos ----
    def test_reescaneo_mismo_punto_registra_dos_filas_progreso_unico(self):
        # Segundo punto para que la ronda NO se complete tras 1 escaneo.
        cp2 = PuntoControl.objects.create(
            instalacion_id=10, nombre="Porton Sur", lat=str(LAT), lng=str(LNG),
            tolerancia_mts=30, validar_posicion=True,
            qr_token="22222222-2222-2222-2222-222222222222", activo=True,
        )
        RondaSecuencia.objects.create(ronda=self.ronda, punto_control=cp2, orden=2)

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
        # Re-escaneo permitido: ambas registran arribo.
        self.assertEqual(r1["resultado"], "ok")
        self.assertEqual(r2["resultado"], "ok")
        # DOS filas de arribo para ese punto en la ventana.
        arribos = LibroNovedades.objects.filter(
            punto_control=self.cp, tipo_evento__codigo="arribo",
        ).count()
        self.assertEqual(arribos, 2)
        # ...pero el progreso lo cuenta UNA vez (puntos únicos): 1 de 2.
        self.assertEqual(r2["progreso"]["escaneados"], 1)
        self.assertEqual(r2["progreso"]["total"], 2)


class VentanaAlarmaTests(TestCase):
    """Ventanas de marcaje por alarma (programacion_horario). El bloqueo pasa a
    ser por guardia + ronda + punto + ventana-de-alarma; al entrar la siguiente
    alarma, los QR se reinician. Fallback a turno si no hay alarmas."""

    GUARDIA_B = "ffffffff-ffff-ffff-ffff-ffffffffffff"

    def setUp(self):
        call_command("seed_tipos_evento")
        # Ronda de DÍA COMPLETO (00:00–23:59:59) para que _ronda_para_ahora (que
        # usa la hora REAL del servidor) siempre la encuentre; las VENTANAS las
        # controlamos pasando `ahora` explícito a registrar_escaneo.
        self.ronda = Ronda.objects.create(
            cliente_id=1, instalacion_id=10, nombre="Ronda Día",
            fecha_inicio=date(2026, 1, 1),
            hora_inicio=time(0, 0, 0), hora_fin=time(23, 59, 59),
        )
        self.cp = PuntoControl.objects.create(
            instalacion_id=10, nombre="P1", lat=str(LAT), lng=str(LNG),
            tolerancia_mts=30, validar_posicion=True,
            qr_token="cccccccc-cccc-cccc-cccc-cccccccccccc", activo=True,
        )
        RondaSecuencia.objects.create(ronda=self.ronda, punto_control=self.cp, orden=1)
        # Alarmas 12:00 / 14:00 / 16:00 -> ventanas 12:00–13:59:59, 14:00–15:59:59, 16:00–fin.
        prog = Programacion.objects.create(ronda=self.ronda, repite="todos_los_dias", activo=True)
        for i, (h, m) in enumerate([(12, 0), (14, 0), (16, 0)], start=1):
            ProgramacionHorario.objects.create(programacion=prog, hora=h, minuto=m, orden=i)
        self.hoy = timezone.localtime(timezone.now()).date()

    def _ahora(self, hh, mm, ss=0):
        return timezone.make_aware(datetime.combine(self.hoy, time(hh, mm, ss)))

    def _marcar(self, ahora, guardia=GUARDIA, cp=None):
        return registrar_escaneo(
            instalacion_id=10, guardia_keycloak_id=guardia,
            qr_token=(cp or self.cp).qr_token, lat=LAT, lng=LNG, texto=None, ahora=ahora,
        )

    # ---- derivación de la ventana ----
    def test_ventana_alarma_subventana_correcta(self):
        ini, fin = _ventana_alarma(self.ronda, self._ahora(12, 30))
        self.assertEqual(ini, self._ahora(12, 0))
        self.assertEqual(fin, self._ahora(13, 59, 59))  # siguiente alarma (14:00) − 1s

    def test_ultima_ventana_hasta_hora_fin(self):
        ini, fin = _ventana_alarma(self.ronda, self._ahora(16, 30))
        self.assertEqual(ini, self._ahora(16, 0))
        self.assertEqual(fin, self._ahora(23, 59, 59))  # hora_fin del turno

    # ---- bloqueo / reinicio por ventana ----
    def test_mismo_qr_dos_ventanas_permitido(self):
        r1 = self._marcar(self._ahora(12, 30))   # ventana 1
        r2 = self._marcar(self._ahora(14, 30))   # ventana 2
        self.assertEqual(r1["resultado"], "ok")
        self.assertEqual(r2["resultado"], "ok")
        self.assertEqual(
            LibroNovedades.objects.filter(punto_control=self.cp, tipo_evento__codigo="arribo").count(), 2
        )

    def test_mismo_qr_misma_ventana_reescanea(self):
        r1 = self._marcar(self._ahora(12, 30))
        r2 = self._marcar(self._ahora(12, 45))   # misma ventana 1: re-escaneo permitido
        self.assertEqual(r1["resultado"], "ok")
        self.assertEqual(r2["resultado"], "ok")
        # Dos filas para el mismo punto en la misma ventana (cada escaneo registra).
        self.assertEqual(
            LibroNovedades.objects.filter(punto_control=self.cp, tipo_evento__codigo="arribo").count(), 2
        )

    def test_conteo_por_guardia_independiente(self):
        a = self._marcar(self._ahora(12, 30), guardia=GUARDIA)
        b = self._marcar(self._ahora(12, 30), guardia=self.GUARDIA_B)  # otro guardia, misma ventana
        self.assertEqual(a["resultado"], "ok")
        self.assertEqual(b["resultado"], "ok")  # independiente: B no bloqueado por A
        self.assertEqual(
            LibroNovedades.objects.filter(punto_control=self.cp, tipo_evento__codigo="arribo").count(), 2
        )

    def test_progreso_se_reinicia_en_la_siguiente_ventana(self):
        cp2 = PuntoControl.objects.create(
            instalacion_id=10, nombre="P2", lat=str(LAT), lng=str(LNG),
            tolerancia_mts=30, validar_posicion=True,
            qr_token="dddddddd-dddd-dddd-dddd-dddddddddddd", activo=True,
        )
        RondaSecuencia.objects.create(ronda=self.ronda, punto_control=cp2, orden=2)
        # Ventana 1: completa los 2 puntos -> 2/2.
        self._marcar(self._ahora(12, 10), cp=self.cp)
        r = self._marcar(self._ahora(12, 20), cp=cp2)
        self.assertEqual(r["progreso"]["escaneados"], 2)
        # Ventana 2: marcar 1 punto -> progreso 1/2 (RESET, no 3/2).
        r2 = self._marcar(self._ahora(14, 10), cp=self.cp)
        self.assertEqual(r2["progreso"]["escaneados"], 1)
        self.assertEqual(r2["progreso"]["total"], 2)

    def test_antes_de_la_primera_alarma_sin_ventana(self):
        antes = LibroNovedades.objects.count()
        res = self._marcar(self._ahora(11, 30))  # antes de 12:00
        self.assertEqual(res["resultado"], "sin_ventana_activa")
        self.assertEqual(LibroNovedades.objects.count(), antes)  # no registra

    # ---- cruce de medianoche (unit test del helper) ----
    def test_cruce_medianoche_ventana_madrugada(self):
        ronda_noche = Ronda.objects.create(
            cliente_id=1, instalacion_id=10, nombre="Ronda Noche",
            fecha_inicio=date(2026, 1, 1),
            hora_inicio=time(22, 0, 0), hora_fin=time(6, 0, 0),  # cruza medianoche
        )
        prog = Programacion.objects.create(ronda=ronda_noche, repite="todos_los_dias", activo=True)
        for i, (h, m) in enumerate([(23, 0), (1, 0), (3, 0)], start=1):
            ProgramacionHorario.objects.create(programacion=prog, hora=h, minuto=m, orden=i)
        # ref de madrugada (01:30) -> ventana [01:00, 02:59:59] del mismo día.
        ref = self._ahora(1, 30)
        ini, fin = _ventana_alarma(ronda_noche, ref)
        self.assertEqual(ini, self._ahora(1, 0))
        self.assertEqual(fin, self._ahora(2, 59, 59))  # siguiente alarma (03:00) − 1s

    # ---- fallback: ronda SIN alarmas = ventana de turno; re-escaneo permitido ----
    def test_fallback_sin_alarmas_reescanea_en_el_turno(self):
        ronda_fb = Ronda.objects.create(
            cliente_id=1, instalacion_id=12, nombre="Ronda Día",
            fecha_inicio=date(2026, 1, 1),
            hora_inicio=time(0, 0, 0), hora_fin=time(23, 59, 59),
        )
        cp_fb = PuntoControl.objects.create(
            instalacion_id=12, nombre="FB", lat=str(LAT), lng=str(LNG),
            tolerancia_mts=30, validar_posicion=True,
            qr_token="eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee", activo=True,
        )
        RondaSecuencia.objects.create(ronda=ronda_fb, punto_control=cp_fb, orden=1)
        # Sin programacion -> ventana = turno completo. Dos marcas del MISMO punto
        # en el mismo turno: ambas registran (re-escaneo permitido dentro de la ventana).
        r1 = registrar_escaneo(instalacion_id=12, guardia_keycloak_id=GUARDIA,
                               qr_token=cp_fb.qr_token, lat=LAT, lng=LNG, texto=None, ahora=self._ahora(12, 0))
        r2 = registrar_escaneo(instalacion_id=12, guardia_keycloak_id=GUARDIA,
                               qr_token=cp_fb.qr_token, lat=LAT, lng=LNG, texto=None, ahora=self._ahora(16, 0))
        self.assertEqual(r1["resultado"], "ok")
        self.assertEqual(r2["resultado"], "ok")
        self.assertEqual(
            LibroNovedades.objects.filter(punto_control=cp_fb, tipo_evento__codigo="arribo").count(), 2
        )
