"""
Tests del sync del espejo (apps/espejo/sync.py) — filtro OPCIONAL por razon_social.

La BD de Ayres se simula con un cursor falso (no hay conexión real 'ayres' en los
tests). El fake reproduce lo esencial del SQL:
  - SELECT * FROM clientes [WHERE LOWER(razon_social) LIKE ...]  (prefiltro por palabra,
    acento-insensible, como la collation de MySQL: usamos _norm en ambos lados).
  - SELECT * FROM instalaciones [WHERE cliente_id IN (...)]  o  todas (SELECT directo).
"""
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase

from apps.espejo import sync
from apps.espejo.models import Cliente, Instalacion
from apps.espejo.sync import _norm


def _cliente(id, razon):
    """Fila de cliente con todos los campos NOT NULL (como el SELECT * de Ayres)."""
    return {
        "id": id, "razon_social": razon, "rut": f"{id}-9", "tipo": "privado",
        "moneda": "CLP", "cantidad_admin_contrato": 1, "renovacion_automatica": False,
        "reajuste_ipc": False, "reajuste_imm": False, "estado": "activo",
    }


def _instalacion(id, cliente_id, nombre):
    """Fila de instalación con todos los campos NOT NULL. 'codigo'/'qr' NO vienen
    de Ayres (los pone/preserva el sync)."""
    return {
        "id": id, "cliente_id": cliente_id, "nombre": nombre, "categoria": "media",
        "dotacion_requerida": 1, "cantidad_guardias": 1, "cantidad_supervisores": 0,
        "permite_ley_inclusion": False, "estado": "activo",
    }


class _FakeCursor:
    """Cursor falso: responde execute/description/fetchall como el real y registra
    el SQL ejecutado para poder afirmar 'SELECT directo' vs 'WHERE IN'."""

    def __init__(self, clientes, instalaciones):
        self._clientes = clientes
        self._instalaciones = instalaciones
        self._rows = []
        self._cols = []
        self.sql = []  # log de (sql, params)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        params = params or []
        self.sql.append((sql, params))
        low = sql.lower()
        if "from clientes" in low:
            rows = self._clientes
            if params:  # prefiltro OR por palabra (acento-insensible, como MySQL)
                palabras = [p.strip("%") for p in params]
                rows = [c for c in rows
                        if any(pal in _norm(c.get("razon_social")) for pal in palabras)]
            self._set(rows)
        elif "from instalaciones" in low:
            rows = self._instalaciones
            if params:  # WHERE cliente_id IN (...)
                ids = set(params)
                rows = [i for i in rows if i.get("cliente_id") in ids]
            self._set(rows)

    def _set(self, rows):
        cols = []
        for r in rows:
            for k in r:
                if k not in cols:
                    cols.append(k)
        self._cols = cols
        self._rows = rows

    @property
    def description(self):
        return [(c,) for c in self._cols]

    def fetchall(self):
        return [tuple(r.get(c) for c in self._cols) for r in self._rows]


class _FakeConns:
    """Reemplaza `connections`: expone .databases y connections['ayres'].cursor()."""

    def __init__(self, cursor):
        self.databases = {"ayres": {}, "default": {}}
        self._cursor = cursor

    def __getitem__(self, alias):
        cur = self._cursor
        return SimpleNamespace(cursor=lambda: cur)


class SyncFiltroRazonSocialTests(TestCase):
    CLIENTES = [
        _cliente(1, "Municipalidad de Las Condes"),
        _cliente(2, "Retail Parque Arauco S.A."),
        _cliente(3, "Clínica Alemana de Santiago"),  # con tilde a propósito
    ]
    INSTALACIONES = [
        _instalacion(10, 1, "Puesto Las Condes"),
        _instalacion(11, 2, "Mall Parque Arauco"),
        _instalacion(12, 3, "Clínica Alemana"),
    ]

    def _correr(self, terminos, dry_run=False):
        cur = _FakeCursor(self.CLIENTES, self.INSTALACIONES)
        with self.settings(SYNC_CLIENTES_RAZON=terminos), \
                patch("apps.espejo.sync.connections", _FakeConns(cur)):
            resumen = sync.sincronizar(dry_run=dry_run, escribir=lambda *a, **k: None)
        return resumen, cur

    # ---- sin términos => TODOS (el fix del bug del refine) ----
    def test_sin_terminos_trae_todos(self):
        _resumen, cur = self._correr([])
        self.assertEqual(Cliente.objects.count(), 3)        # NO 0 (bug evitado)
        self.assertEqual(Instalacion.objects.count(), 3)
        self.assertEqual(set(Cliente.objects.values_list("id", flat=True)), {1, 2, 3})
        # Instalaciones por SELECT DIRECTO (sin IN gigante).
        sqls = [s.strip().lower() for s, _ in cur.sql]
        self.assertIn("select * from instalaciones", sqls)
        self.assertFalse(any("in (" in s for s in sqls))    # ningún WHERE IN

    # ---- con un término => filtra EXACTAMENTE como hoy ----
    def test_un_termino_filtra_como_hoy(self):
        self._correr(["municipalidad de las condes"])
        self.assertEqual(set(Cliente.objects.values_list("id", flat=True)), {1})
        self.assertEqual(set(Instalacion.objects.values_list("id", flat=True)), {10})
        # No se sincronizó ni cliente 2 ni 3.
        self.assertFalse(Cliente.objects.filter(id__in=[2, 3]).exists())

    def test_un_termino_usa_where_in(self):
        _resumen, cur = self._correr(["municipalidad de las condes"])
        sqls = [s.lower() for s, _ in cur.sql]
        self.assertTrue(any("from instalaciones where cliente_id in (" in s for s in sqls))

    def test_matching_tolerante_tildes_y_orden(self):
        # "clinica alemana" (sin tilde, 2 palabras) matchea "Clínica Alemana de Santiago".
        self._correr(["clinica alemana"])
        self.assertEqual(set(Cliente.objects.values_list("id", flat=True)), {3})
        self.assertEqual(set(Instalacion.objects.values_list("id", flat=True)), {12})

    def test_varios_terminos_or(self):
        # Cualquiera de los términos: clientes 2 y 3 (no el 1).
        self._correr(["parque arauco", "clinica alemana"])
        self.assertEqual(set(Cliente.objects.values_list("id", flat=True)), {2, 3})
        self.assertEqual(set(Instalacion.objects.values_list("id", flat=True)), {11, 12})

    def test_dry_run_no_escribe(self):
        resumen, _cur = self._correr([], dry_run=True)
        self.assertTrue(resumen["dry_run"])
        self.assertEqual(Cliente.objects.count(), 0)        # dry-run: nada escrito
        self.assertEqual(Instalacion.objects.count(), 0)
        self.assertEqual(resumen["clientes_creados"], 3)    # pero cuenta lo que haría

    def test_instalaciones_reciben_codigo_ayr(self):
        # El sync asigna AYR-NNNN a instalaciones nuevas (propio de Rondas).
        self._correr([])
        codigos = set(Instalacion.objects.values_list("codigo", flat=True))
        self.assertEqual(codigos, {"AYR-0001", "AYR-0002", "AYR-0003"})
