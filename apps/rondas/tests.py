"""
Tests del RondaForm: validación de las ALARMAS (programacion_horario) que definen
las ventanas de marcaje. El backend rechaza alarmas duplicadas o fuera del turno,
así nunca llegan ventanas inválidas a la lógica de escaneo.
"""
from django.http import QueryDict
from django.test import TestCase

from apps.checkpoints.models import PuntoControl
from apps.rondas.forms import RondaForm

INSTALACION = 10


class RondaFormAlarmasTests(TestCase):
    def setUp(self):
        # Ronda aleatoria necesita ≥1 checkpoint activo en la instalación.
        PuntoControl.objects.create(
            instalacion_id=INSTALACION, nombre="P1", lat="-33.4", lng="-70.5",
            tolerancia_mts=30, validar_posicion=True, qr_token="qr-1", activo=True,
        )

    def _data(self, horas, minutos, hora_inicio="12:00", hora_fin="18:59"):
        d = QueryDict(mutable=True)
        d["nombre"] = "Ronda Día"
        d["fecha_inicio"] = "2026-01-01"
        d["hora_inicio"] = hora_inicio
        d["hora_fin"] = hora_fin
        d["modo_orden"] = "aleatoria"
        d["repite"] = "todos_los_dias"
        d.setlist("hora", horas)
        d.setlist("minuto", minutos)
        return d

    def test_alarmas_validas_dentro_del_turno(self):
        form = RondaForm(self._data(["12", "14", "16"], ["0", "0", "0"]), instalacion_id=INSTALACION)
        self.assertTrue(form.is_valid(), form.errors)

    def test_alarmas_duplicadas_rechazadas(self):
        form = RondaForm(self._data(["12", "14", "14"], ["0", "0", "0"]), instalacion_id=INSTALACION)
        self.assertFalse(form.is_valid())
        self.assertTrue(any("duplicada" in e.lower() for e in form.non_field_errors()))

    def test_alarma_fuera_del_turno_rechazada(self):
        # Turno 12:00–18:59; alarma 20:00 queda fuera.
        form = RondaForm(self._data(["12", "20"], ["0", "0"]), instalacion_id=INSTALACION)
        self.assertFalse(form.is_valid())
        self.assertTrue(any("fuera del horario" in e.lower() for e in form.non_field_errors()))

    def test_cruce_medianoche_alarma_de_madrugada_valida(self):
        # Turno 22:00–06:00 (cruza medianoche); alarma 01:00 está DENTRO.
        form = RondaForm(
            self._data(["23", "1"], ["0", "0"], hora_inicio="22:00", hora_fin="06:00"),
            instalacion_id=INSTALACION,
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_cruce_medianoche_alarma_fuera_rechazada(self):
        # Turno 22:00–06:00; alarma 12:00 (mediodía) está FUERA.
        form = RondaForm(
            self._data(["23", "12"], ["0", "0"], hora_inicio="22:00", hora_fin="06:00"),
            instalacion_id=INSTALACION,
        )
        self.assertFalse(form.is_valid())
        self.assertTrue(any("fuera del horario" in e.lower() for e in form.non_field_errors()))
