"""
Tests del módulo "Eventos en tiempo real": acceso (SSPP/super_admin), página
global (no exige instalación), y el endpoint JSON con datos resueltos (cliente,
instalación, guardia_nombre, fotos, color) + paginación.
"""
from uuid import UUID

import jwt
from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.espejo.models import Cliente, Instalacion
from apps.novedades.models import (
    CategoriaEvento,
    LibroNovedades,
    LibroNovedadesMedia,
    TipoEvento,
    TipoMedia,
)

SUB = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


@override_settings(STORAGES={
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
})
class TiempoRealTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create(
            username="sspp", keycloak_id=UUID(SUB), first_name="Juan", last_name="Pérez",
        )
        self.client = Client()
        self.client.force_login(self.user)
        self._rol(["super_admin"])

        # Espejo: cliente + instalación para resolver nombres.
        Cliente.objects.create(id=1, razon_social="Municipalidad X", rut="1-9")
        Instalacion.objects.create(id=10, codigo="AYR-0001", cliente_id=1, nombre="Puesto Norte")

        # Catálogo mínimo.
        self.t_novedad = TipoEvento.objects.create(codigo="novedad", nombre="Novedad", categoria=CategoriaEvento.NOVEDAD)
        self.t_sesion = TipoEvento.objects.create(codigo="sesion_inicio", nombre="Inicio de sesión", categoria=CategoriaEvento.SESION)

    def _rol(self, roles):
        token = jwt.encode({"realm_access": {"roles": roles}}, "x", algorithm="HS256")
        s = self.client.session
        s["oidc_access_token"] = token
        s.save()

    def _evento(self, tipo):
        ahora = timezone.now()
        return LibroNovedades.objects.create(
            instalacion_id=10, guardia_keycloak_id=SUB, tipo_evento=tipo,
            timestamp_evento=ahora, timestamp_servidor=ahora, estado="ok", texto="Algo",
        )

    # ---- acceso ----
    def test_index_super_admin_200_pagina_global(self):
        # NO fijamos instalacion_id en sesión: es página global y NO debe exigirla.
        resp = self.client.get(reverse("tiempo_real:index"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Eventos en tiempo real")

    def test_index_sspp_200(self):
        self._rol(["sspp"])
        self.assertEqual(self.client.get(reverse("tiempo_real:index")).status_code, 200)

    def test_index_rol_no_autorizado_redirige(self):
        self._rol(["guardia"])
        resp = self.client.get(reverse("tiempo_real:index"))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("comun:dashboard"))

    # ---- endpoint JSON ----
    def test_data_resuelve_campos_y_paginacion(self):
        self._evento(self.t_novedad)
        resp = self.client.get(reverse("tiempo_real:data"))
        self.assertEqual(resp.status_code, 200)
        d = resp.json()
        self.assertEqual(d["page"], 1)
        self.assertIn("num_pages", d)
        ev = d["eventos"][0]
        self.assertEqual(ev["cliente"], "Municipalidad X")     # resuelto del espejo
        self.assertEqual(ev["instalacion"], "Puesto Norte")
        self.assertEqual(ev["guardia"], "Juan Pérez")          # reusa _nombres_de_guardias
        self.assertEqual(ev["tipo"], "Novedad")
        self.assertEqual(ev["color"], "fila-novedad")          # color por tipo
        self.assertEqual(ev["punto"], "—")                     # sin punto de control

    def test_novedad_con_foto_expone_url(self):
        ev = self._evento(self.t_novedad)
        LibroNovedadesMedia.objects.create(
            libro_novedades=ev, tipo=TipoMedia.FOTO, path="novedades/abc.jpg",
        )
        fila = self.client.get(reverse("tiempo_real:data")).json()["eventos"][0]
        self.assertTrue(fila["tiene_boton"])
        self.assertEqual(len(fila["fotos"]), 1)
        self.assertIn("abc.jpg", fila["fotos"][0])

    def test_sesion_inicio_sin_foto_boton_pero_sin_imagenes(self):
        # sesion_inicio SIN media: el botón se muestra igual (modal dirá "sin imagen").
        self._evento(self.t_sesion)
        fila = self.client.get(reverse("tiempo_real:data")).json()["eventos"][0]
        self.assertEqual(fila["color"], "fila-sesion-inicio")
        self.assertTrue(fila["tiene_boton"])
        self.assertEqual(fila["fotos"], [])

    def test_arribo_sin_boton_de_fotos(self):
        t_arribo = TipoEvento.objects.create(codigo="arribo", nombre="Arribo", categoria=CategoriaEvento.RONDA)
        self._evento(t_arribo)
        fila = self.client.get(reverse("tiempo_real:data")).json()["eventos"][0]
        self.assertFalse(fila["tiene_boton"])
        self.assertEqual(fila["color"], "fila-arribo")

    def test_data_no_exige_instalacion(self):
        # Sin instalacion_id en sesión, el endpoint responde normal (página global).
        self._evento(self.t_novedad)
        self.assertEqual(self.client.get(reverse("tiempo_real:data")).status_code, 200)
