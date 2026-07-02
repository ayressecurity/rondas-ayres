"""
Tests del módulo Clientes: paginación (15/página) + buscador por razón social
(icontains, backend) conservando el término entre páginas, sin romper la
selección de cliente por fila.
"""
import jwt
from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from apps.espejo.models import Cliente


@override_settings(STORAGES={
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
})
class ClientesListadoTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create(username="u")
        self.client = Client()
        self.client.force_login(self.user)

    def _crear(self, n, prefijo="Cliente"):
        for i in range(1, n + 1):
            Cliente.objects.create(id=i, razon_social=f"{prefijo} {i:02d}", rut=f"{i}-9")

    # ---- paginación (15/página) ----
    def test_primera_pagina_muestra_15(self):
        self._crear(20)
        resp = self.client.get(reverse("clientes:index"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.context["page_obj"].object_list), 15)
        self.assertEqual(resp.context["page_obj"].paginator.num_pages, 2)

    def test_segunda_pagina_muestra_resto(self):
        self._crear(20)
        resp = self.client.get(reverse("clientes:index") + "?page=2")
        self.assertEqual(len(resp.context["page_obj"].object_list), 5)
        self.assertTrue(resp.context["page_obj"].has_previous())
        self.assertFalse(resp.context["page_obj"].has_next())

    # ---- buscador (icontains, backend) ----
    def test_buscador_filtra_por_razon_social(self):
        Cliente.objects.create(id=1, razon_social="Municipalidad de Las Condes", rut="1-9")
        Cliente.objects.create(id=2, razon_social="Retail Parque Arauco", rut="2-9")
        Cliente.objects.create(id=3, razon_social="Clínica Alemana", rut="3-9")
        resp = self.client.get(reverse("clientes:index"), {"q": "parque"})  # case-insensitive
        ids = [c.id for c in resp.context["page_obj"].object_list]
        self.assertEqual(ids, [2])
        self.assertEqual(resp.context["q"], "parque")   # término conservado en el input

    def test_buscador_sin_resultados_no_rompe(self):
        self._crear(3)
        resp = self.client.get(reverse("clientes:index"), {"q": "zzz-no-existe"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.context["page_obj"].object_list), 0)
        self.assertContains(resp, "No hay clientes que coincidan")

    def test_termino_se_conserva_entre_paginas(self):
        self._crear(20, prefijo="Alfa")   # 20 coinciden con "Alfa" -> 2 páginas
        resp = self.client.get(reverse("clientes:index"), {"q": "Alfa"})
        self.assertEqual(resp.context["page_obj"].paginator.num_pages, 2)
        self.assertIn("q=Alfa", resp.context["query_sin_page"])
        # El enlace "Siguiente" del paginador conserva el término de búsqueda.
        self.assertContains(resp, "?q=Alfa&page=2")

    # ---- la selección de cliente sigue igual ----
    def test_seleccion_de_cliente_intacta(self):
        Cliente.objects.create(id=7, razon_social="Cliente 7", rut="7-9")
        resp = self.client.post(reverse("clientes:seleccionar", args=[7]))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(self.client.session["cliente_id"], 7)


class ClientesRolClienteBloqueadoTests(TestCase):
    """El rol 'cliente' (empresa externa) NO gestiona clientes: no ve la lista
    global, no puede elegir otro cliente ni limpiar el suyo (queda amarrado al del
    token por el middleware). Los demás roles no cambian."""

    def setUp(self):
        self.user = get_user_model().objects.create(username="cli")
        self.client = Client()
        self.client.force_login(self.user)
        # Su cliente (400) y otro ajeno (500), ambos vigentes en el espejo.
        Cliente.objects.create(id=400, razon_social="Muni Las Condes", rut="400-9")
        Cliente.objects.create(id=500, razon_social="Otro Cliente", rut="500-9")
        self._rol_cliente(400)

    def _rol_cliente(self, cliente_id):
        token = jwt.encode(
            {"realm_access": {"roles": ["cliente"]}, "cliente_id": str(cliente_id)},
            "x", algorithm="HS256",
        )
        s = self.client.session
        s["oidc_access_token"] = token
        s.save()

    def test_index_redirige_a_instalaciones(self):
        resp = self.client.get(reverse("clientes:index"))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("instalaciones:index"))

    def test_no_puede_seleccionar_otro_cliente(self):
        # Intenta fijar el cliente 500 por URL -> bloqueado; sigue amarrado al 400.
        resp = self.client.post(reverse("clientes:seleccionar", args=[500]))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("instalaciones:index"))
        self.assertEqual(self.client.session["cliente_id"], 400)   # forzado por el middleware

    def test_cambiar_bloqueado(self):
        resp = self.client.get(reverse("clientes:cambiar"))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("instalaciones:index"))
        self.assertEqual(self.client.session["cliente_id"], 400)   # no se limpió
