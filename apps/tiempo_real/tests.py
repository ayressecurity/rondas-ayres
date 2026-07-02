"""
Tests del módulo "Eventos en tiempo real": acceso (SSPP/super_admin), página
global (no exige instalación), y el endpoint JSON con datos resueltos (cliente,
instalación, guardia_nombre, fotos, color) + paginación.
"""
from datetime import timedelta
from uuid import UUID

import jwt
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
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

    def _evento(self, tipo, comentario=None):
        ahora = timezone.now()
        return LibroNovedades.objects.create(
            instalacion_id=10, guardia_keycloak_id=SUB, tipo_evento=tipo,
            timestamp_evento=ahora, timestamp_servidor=ahora, estado="ok", texto="Algo",
            comentario_central=comentario,
        )

    # ---- acceso ----
    def test_index_super_admin_200_pagina_global(self):
        # NO fijamos instalacion_id en sesión: es página global y NO debe exigirla.
        resp = self.client.get(reverse("tiempo_real:index"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Central de monitoreo")   # renombrado (3.2)

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

    # ---- FIX orden: por HORA REAL del evento (timestamp_evento), no por id ----
    def _evento_en(self, ts_evento, ts_servidor):
        return LibroNovedades.objects.create(
            instalacion_id=10, guardia_keycloak_id=SUB, tipo_evento=self.t_novedad,
            timestamp_evento=ts_evento, timestamp_servidor=ts_servidor, estado="ok", texto="x",
        )

    def test_orden_por_hora_real_no_por_id(self):
        base = timezone.now()
        # Reciente (hora real T2), creado PRIMERO -> id MENOR.
        reciente = self._evento_en(base, base)
        # OFFLINE: hora real T1 anterior, pero llega DESPUÉS -> id MAYOR.
        offline = self._evento_en(base - timedelta(minutes=1), base + timedelta(minutes=5))
        self.assertGreater(offline.id, reciente.id)  # el offline llegó después (id mayor)

        eventos = self.client.get(reverse("tiempo_real:data")).json()["eventos"]
        # Orden por HORA REAL desc: el reciente (T2) va ANTES que el offline (T1),
        # aunque el offline tenga id mayor. Con -id salía al revés (el bug).
        self.assertEqual([e["id"] for e in eventos], [reciente.id, offline.id])

    def test_desempate_por_id_misma_hora(self):
        base = timezone.now()
        a = self._evento_en(base, base)          # misma hora exacta, id menor
        b = self._evento_en(base, base)          # misma hora exacta, id mayor
        eventos = self.client.get(reverse("tiempo_real:data")).json()["eventos"]
        # Empate de hora -> desempata por -id (estable): el de id mayor primero.
        self.assertEqual([e["id"] for e in eventos], [b.id, a.id])

    # ---- FIX fotos en refresco: el JSON de data trae 'fotos' por fila ----
    def test_data_fotos_por_fila_con_y_sin_imagen(self):
        # El JS (filaHTML) decide la columna Media por ev.fotos: el JSON debe traerlo
        # (lista de URLs si hay imagen; [] si no) — igual que el render inicial.
        con = self._evento(self.t_novedad)
        LibroNovedadesMedia.objects.create(
            libro_novedades=con, tipo=TipoMedia.FOTO, path="novedades/con.jpg",
        )
        sin = self._evento(self.t_novedad)  # novedad SIN foto
        filas = {f["id"]: f for f in self.client.get(reverse("tiempo_real:data")).json()["eventos"]}
        self.assertEqual(len(filas[con.id]["fotos"]), 1)
        self.assertIn("con.jpg", filas[con.id]["fotos"][0])
        self.assertEqual(filas[sin.id]["fotos"], [])

    def test_data_fotos_sin_n_mas_uno(self):
        # Las fotos de TODA la página se resuelven en 1 query (_adjuntar_fotos):
        # más eventos con foto NO deben aumentar el nº de queries del endpoint.
        e1 = self._evento(self.t_novedad)
        LibroNovedadesMedia.objects.create(libro_novedades=e1, tipo=TipoMedia.FOTO, path="n/1.jpg")
        with CaptureQueriesContext(connection) as ctx1:
            self.client.get(reverse("tiempo_real:data"))
        base = len(ctx1.captured_queries)

        for i in range(4):  # 4 eventos más, cada uno con su foto
            e = self._evento(self.t_novedad)
            LibroNovedadesMedia.objects.create(libro_novedades=e, tipo=TipoMedia.FOTO, path=f"n/{i}.jpg")
        with CaptureQueriesContext(connection) as ctx2:
            self.client.get(reverse("tiempo_real:data"))

        # Constante: sin N+1 (ni por evento ni por foto).
        self.assertEqual(len(ctx2.captured_queries), base)

    # ---- 3.2: rol cenapoc (ver la tabla) ----
    def test_index_cenapoc_200(self):
        self._rol(["cenapoc"])
        self.assertEqual(self.client.get(reverse("tiempo_real:index")).status_code, 200)

    # ---- 3.2: separación ver-tabla vs poder-comentar (botón Acción) ----
    # Discriminador robusto: el botón RENDERIZADO server-side trae los valores ya
    # resueltos ('data-id="1" data-cliente=...'); el JS los arma por concatenación,
    # así que esa cadena literal SOLO aparece si el servidor pintó el botón en la fila.
    BOTON_FILA = 'data-id="1" data-cliente='

    def test_boton_comentar_visible_super_admin(self):
        self._evento(self.t_novedad)  # setUp deja super_admin
        resp = self.client.get(reverse("tiempo_real:index"))
        self.assertContains(resp, self.BOTON_FILA)
        self.assertContains(resp, "var puedeComentar = true;")   # flag del JS

    def test_boton_comentar_visible_cenapoc(self):
        self._rol(["cenapoc"])
        self._evento(self.t_novedad)
        resp = self.client.get(reverse("tiempo_real:index"))
        self.assertContains(resp, self.BOTON_FILA)
        self.assertContains(resp, "var puedeComentar = true;")

    def test_boton_comentar_oculto_para_sspp(self):
        # sspp VE la tabla pero NO el botón de comentar (celda Acción = "—").
        self._rol(["sspp"])
        self._evento(self.t_novedad)
        resp = self.client.get(reverse("tiempo_real:index"))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, self.BOTON_FILA)             # ningún botón en la fila
        self.assertContains(resp, "var puedeComentar = false;")  # y el JS tampoco pintará

    # ---- 3.2: el JSON de data trae comentario_central + tiene_comentario + flag ----
    def test_data_incluye_comentario_y_flag(self):
        self._evento(self.t_novedad, comentario="Revisar cámara")
        d = self.client.get(reverse("tiempo_real:data")).json()
        self.assertTrue(d["puede_comentar"])          # super_admin
        ev = d["eventos"][0]
        self.assertEqual(ev["comentario_central"], "Revisar cámara")
        self.assertTrue(ev["tiene_comentario"])

    def test_data_puede_comentar_false_para_sspp(self):
        self._rol(["sspp"])
        self._evento(self.t_novedad)
        d = self.client.get(reverse("tiempo_real:data")).json()
        self.assertFalse(d["puede_comentar"])
        self.assertEqual(d["eventos"][0]["comentario_central"], "")
        self.assertFalse(d["eventos"][0]["tiene_comentario"])

    # ---- 3.2: endpoint comentar ----
    def _comentar(self, ev_id, comentario):
        return self.client.post(
            reverse("tiempo_real:comentar"), {"id": ev_id, "comentario": comentario}
        )

    def test_comentar_super_admin_guarda_y_edita(self):
        ev = self._evento(self.t_novedad)
        resp = self._comentar(ev.id, "Primer comentario")
        self.assertEqual(resp.status_code, 200)
        d = resp.json()
        self.assertEqual(d["comentario_central"], "Primer comentario")
        self.assertTrue(d["tiene_comentario"])
        ev.refresh_from_db()
        self.assertEqual(ev.comentario_central, "Primer comentario")
        # Editable: un segundo POST lo sobrescribe.
        self._comentar(ev.id, "Comentario editado")
        ev.refresh_from_db()
        self.assertEqual(ev.comentario_central, "Comentario editado")

    def test_comentar_cenapoc_guarda(self):
        self._rol(["cenapoc"])
        ev = self._evento(self.t_novedad)
        self.assertEqual(self._comentar(ev.id, "Desde cenapoc").status_code, 200)
        ev.refresh_from_db()
        self.assertEqual(ev.comentario_central, "Desde cenapoc")

    def test_comentar_vacio_borra_a_null(self):
        ev = self._evento(self.t_novedad, comentario="Tenía comentario")
        resp = self._comentar(ev.id, "   ")   # solo espacios -> NULL
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["tiene_comentario"])
        ev.refresh_from_db()
        self.assertIsNone(ev.comentario_central)

    def test_comentar_sspp_403_no_guarda(self):
        self._rol(["sspp"])
        ev = self._evento(self.t_novedad)
        resp = self._comentar(ev.id, "No debería guardarse")
        self.assertEqual(resp.status_code, 403)
        ev.refresh_from_db()
        self.assertIsNone(ev.comentario_central)   # sin cambios

    def test_comentar_evento_inexistente_404(self):
        self.assertEqual(self._comentar(999999, "x").status_code, 404)

    def test_comentar_sin_login_redirige(self):
        self.client.logout()
        ev = self._evento(self.t_novedad)
        resp = self._comentar(ev.id, "x")
        self.assertEqual(resp.status_code, 302)   # @login_required -> login


@override_settings(STORAGES={
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
})
class TiempoRealAislamientoClienteTests(TestCase):
    """3.3a: la Central aísla por cliente. El rol 'cliente' entra PERO solo ve los
    eventos de instalaciones de SU cliente; super/sspp/cenapoc siguen viendo todo;
    un 'cliente' sin cliente resoluble no ve NADA (nunca cae a "ver todos")."""

    def setUp(self):
        self.user = get_user_model().objects.create(username="cli", keycloak_id=UUID(SUB))
        self.client = Client()
        self.client.force_login(self.user)
        # Dos clientes, una instalación y un evento cada uno.
        Cliente.objects.create(id=400, razon_social="Muni Las Condes", rut="400-9")
        Cliente.objects.create(id=500, razon_social="Otro Cliente", rut="500-9")
        Instalacion.objects.create(id=40, codigo="AYR-0040", cliente_id=400, nombre="Inst 400")
        Instalacion.objects.create(id=50, codigo="AYR-0050", cliente_id=500, nombre="Inst 500")
        self.t = TipoEvento.objects.create(codigo="novedad", nombre="Novedad", categoria=CategoriaEvento.NOVEDAD)
        self.ev400 = self._evento(40)
        self.ev500 = self._evento(50)

    def _evento(self, inst_id):
        ahora = timezone.now()
        return LibroNovedades.objects.create(
            instalacion_id=inst_id, guardia_keycloak_id=SUB, tipo_evento=self.t,
            timestamp_evento=ahora, timestamp_servidor=ahora, estado="ok", texto="x",
        )

    def _rol(self, roles, cliente_id=None):
        claims = {"realm_access": {"roles": roles}}
        if cliente_id is not None:
            claims["cliente_id"] = str(cliente_id)
        s = self.client.session
        s["oidc_access_token"] = jwt.encode(claims, "x", algorithm="HS256")
        s.save()

    def _ids_data(self):
        return [e["id"] for e in self.client.get(reverse("tiempo_real:data")).json()["eventos"]]

    def test_cliente_solo_ve_sus_eventos(self):
        self._rol(["cliente"], cliente_id=400)
        ids = self._ids_data()
        self.assertIn(self.ev400.id, ids)
        self.assertNotIn(self.ev500.id, ids)   # NO ve el de otro cliente

    def test_cliente_puede_entrar_a_la_central(self):
        self._rol(["cliente"], cliente_id=400)
        self.assertEqual(self.client.get(reverse("tiempo_real:index")).status_code, 200)

    def test_cliente_sin_cliente_id_no_ve_nada(self):
        self._rol(["cliente"], cliente_id=None)   # claim ausente
        self.assertEqual(self._ids_data(), [])    # vacío, nunca "todos"

    def test_cliente_no_puede_comentar(self):
        # El rol cliente ve la tabla pero NO comenta (403), ni siquiera de lo suyo.
        self._rol(["cliente"], cliente_id=400)
        resp = self.client.post(
            reverse("tiempo_real:comentar"), {"id": self.ev400.id, "comentario": "x"}
        )
        self.assertEqual(resp.status_code, 403)
        self.ev400.refresh_from_db()
        self.assertIsNone(self.ev400.comentario_central)

    def test_super_admin_ve_todos(self):
        self._rol(["super_admin"])
        ids = self._ids_data()
        self.assertIn(self.ev400.id, ids)
        self.assertIn(self.ev500.id, ids)

    def test_sspp_ve_todos(self):
        self._rol(["sspp"])
        ids = self._ids_data()
        self.assertIn(self.ev400.id, ids)
        self.assertIn(self.ev500.id, ids)
