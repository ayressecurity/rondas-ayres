"""
Tests de Control Vehicular: alta válida, mapeo enum (valor guardado / etiqueta
mostrada), validaciones (kilometraje, requeridos), orden de la lista y acceso.
"""
from uuid import UUID

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from apps.control_vehicular.models import Vehiculo

SUB = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"  # sub con guiones del usuario


# El storage de estáticos de prod es ManifestStaticFilesStorage (exige
# collectstatic). En tests renderizamos base.html ({% static %}), así que usamos
# el storage simple para no depender del manifest. Solo afecta a estos tests.
@override_settings(STORAGES={
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
})
class ControlVehicularTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create(username="u", keycloak_id=UUID(SUB))
        self.client = Client()
        self.client.force_login(self.user)
        # Contexto del sidebar (cliente + instalación), como el resto de módulos.
        s = self.client.session
        s["cliente_id"] = 1
        s["instalacion_id"] = 10
        s["instalacion_nombre"] = "Inst"
        s.save()

    def _payload(self, **over):
        d = {
            "desplazamiento": "entrada",
            "recinto": "Bodega Municipal",
            "ppu": "ABCD12",
            "kilometraje": "15000",
            "tipo_vehiculo": "station_wagon",
            "nombre_conductor": "Juan Perez",
            "codigo_conductor": "C-001",
            "turno": "primer_turno",
        }
        d.update(over)
        return d

    # ---- alta válida ----
    def test_crear_valido_guarda_todo(self):
        resp = self.client.post(reverse("control_vehicular:nuevo"), self._payload())
        self.assertRedirects(resp, reverse("control_vehicular:index"))
        v = Vehiculo.objects.get()
        self.assertEqual(v.desplazamiento, "entrada")
        self.assertEqual(v.recinto, "Bodega Municipal")
        self.assertEqual(v.ppu, "ABCD12")
        self.assertEqual(v.kilometraje, 15000)
        self.assertEqual(v.tipo_vehiculo, "station_wagon")
        self.assertEqual(v.nombre_conductor, "Juan Perez")
        self.assertEqual(v.codigo_conductor, "C-001")
        self.assertEqual(v.turno, "primer_turno")
        # Identidad del usuario logueado, CON guiones (no del form).
        self.assertEqual(v.registrado_keycloak_id, SUB)
        self.assertIsNotNone(v.creado_en)

    # ---- enum: guarda el valor, muestra la etiqueta ----
    def test_enum_valor_y_etiqueta(self):
        self.client.post(reverse("control_vehicular:nuevo"),
                         self._payload(turno="primer_turno", tipo_vehiculo="furgon"))
        v = Vehiculo.objects.get()
        self.assertEqual(v.turno, "primer_turno")             # valor del enum
        self.assertEqual(v.get_turno_display(), "1er turno")  # etiqueta legible
        self.assertEqual(v.tipo_vehiculo, "furgon")
        self.assertEqual(v.get_tipo_vehiculo_display(), "Furgón")

    # ---- validaciones ----
    def test_kilometraje_negativo_rechazado(self):
        resp = self.client.post(reverse("control_vehicular:nuevo"), self._payload(kilometraje="-5"))
        self.assertEqual(resp.status_code, 200)  # re-render con error
        self.assertEqual(Vehiculo.objects.count(), 0)

    def test_kilometraje_no_numerico_rechazado(self):
        resp = self.client.post(reverse("control_vehicular:nuevo"), self._payload(kilometraje="abc"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Vehiculo.objects.count(), 0)

    def test_campo_requerido_vacio_rechazado(self):
        resp = self.client.post(reverse("control_vehicular:nuevo"), self._payload(ppu=""))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Vehiculo.objects.count(), 0)

    def test_recinto_invalido_rechazado(self):
        # Solo se aceptan las 10 opciones del dropdown.
        resp = self.client.post(reverse("control_vehicular:nuevo"), self._payload(recinto="Otro"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Vehiculo.objects.count(), 0)

    def _crear(self, ppu):
        return Vehiculo.objects.create(
            desplazamiento="entrada", recinto="Bodega Municipal", ppu=ppu,
            kilometraje=1, tipo_vehiculo="auto", nombre_conductor="x",
            codigo_conductor="c", turno="primer_turno", registrado_keycloak_id=SUB,
        )

    # ---- lista: más reciente primero ----
    def test_lista_mas_reciente_primero(self):
        from datetime import timedelta

        from django.utils import timezone

        v_viejo = self._crear("AAAA11")
        self._crear("BBBB22")  # más reciente
        # creado_en es auto_now_add y en el test quedan casi iguales; forzamos
        # que el primero sea anterior para validar el orden de la tabla.
        Vehiculo.objects.filter(id=v_viejo.id).update(creado_en=timezone.now() - timedelta(hours=1))

        resp = self.client.get(reverse("control_vehicular:index"))
        self.assertEqual(resp.status_code, 200)
        cuerpo = resp.content.decode()
        self.assertLess(cuerpo.index("BBBB22"), cuerpo.index("AAAA11"))  # B (más nuevo) arriba

    def test_lista_vacia_mensaje(self):
        resp = self.client.get(reverse("control_vehicular:index"))
        self.assertContains(resp, "Aún no hay registros vehiculares")

    # ---- acceso ----
    def test_sin_login_redirige(self):
        self.client.logout()
        resp = self.client.get(reverse("control_vehicular:index"))
        self.assertEqual(resp.status_code, 302)  # a login (OIDC)

    def test_sin_instalacion_redirige(self):
        s = self.client.session
        s.pop("instalacion_id", None)
        s.save()
        resp = self.client.get(reverse("control_vehicular:index"))
        self.assertEqual(resp.status_code, 302)  # @requiere_instalacion
