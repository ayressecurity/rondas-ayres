"""
Sincronizacion del espejo de Ayres360 -> tablas locales cliente, instalacion.

Ayres360 (Servidor 3) es un MySQL externo de SOLO LECTURA: se lee via raw SQL
sobre la conexion "ayres" (connections["ayres"]) y se hace upsert en el espejo
(BD default). Sus tablas son `clientes` e `instalaciones` (BD ayres_security);
las columnas coinciden con nuestro esquema EXCEPTO:
  - Ayres trae `user_id`  -> lo IGNORAMOS.
  - Ayres NO trae `codigo` -> el AYR-00XX es propio de Rondas (se asigna aqui).

Filtro OPCIONAL por razon_social (SYNC_CLIENTES_RAZON): si trae valores, se
sincronizan solo esos clientes y sus instalaciones; si esta VACIO (default), se
sincronizan TODOS los clientes y TODAS sus instalaciones.
"""
import json
import re
import unicodedata
from contextlib import nullcontext

from django.conf import settings
from django.db import connections, transaction

from apps.espejo.models import Cliente, Instalacion

# Columnas del espejo a copiar tal cual desde Ayres (created_at/updated_at son
# auto en el modelo; 'id' es la clave de busqueda; 'codigo' es propio de Rondas).
CLIENTE_FIELDS = [
    "razon_social", "codigo_cc", "rut", "tipo", "id_licitacion", "valor_mensual_neto",
    "moneda", "cantidad_admin_contrato", "direccion", "email_contacto", "telefono_contacto",
    "nombre_contacto", "fecha_inicio_contrato", "fecha_fin_contrato", "renovacion_automatica",
    "reajuste_ipc", "reajuste_imm", "porcentaje_reajuste_ipc", "porcentaje_variacion_imm",
    "periodicidad_reajuste", "estado", "deleted_at",
]
# IMPORTANTE: 'codigo' y 'qr' son campos PROPIOS de Rondas: NUNCA deben entrar en
# esta lista (el sync los pisaría con None en cada sincronización). Que estén
# AUSENTES aquí es justo lo que los preserva (update_or_create solo escribe las
# columnas de 'defaults'). Hay un test-guardia en apps/dispositivos/tests.py.
INSTALACION_FIELDS = [
    "cliente_id", "nombre", "categoria", "direccion", "region", "comuna", "latitud",
    "longitud", "dotacion_requerida", "cantidad_guardias", "cantidad_supervisores",
    "certificaciones_requeridas", "edad_minima_hombres", "edad_maxima_hombres",
    "edad_minima_mujeres", "edad_maxima_mujeres", "requisito_idioma", "experiencia_requerida",
    "capacitacion_requerida", "habilidades_especificas", "permite_ley_inclusion",
    "valor_turno_extra", "valor_jornada_ordinaria", "estado", "observaciones", "deleted_at",
]


# ---------- helpers de texto (match de razon_social) ----------
def _norm(valor):
    """minusculas, sin tildes, espacios colapsados."""
    if not valor:
        return ""
    s = unicodedata.normalize("NFKD", str(valor))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip().lower()


def _coincide(texto, terminos):
    """True si, para algun termino, TODAS sus palabras aparecen en el texto."""
    tn = _norm(texto)
    for term in terminos:
        palabras = _norm(term).split()
        if palabras and all(p in tn for p in palabras):
            return True
    return False


def _prefiltro(columna, terminos):
    """WHERE LOWER(<columna>) LIKE ... (OR por palabra) para prefiltrar en SQL."""
    palabras = sorted({p for t in terminos for p in _norm(t).split()})
    if not palabras:
        return "", []
    clausulas = " OR ".join([f"LOWER({columna}) LIKE %s"] * len(palabras))
    params = [f"%{p}%" for p in palabras]
    return f"WHERE {clausulas}", params


def _filas(cursor):
    """Filas del cursor como lista de dicts (col -> valor)."""
    cols = [c[0] for c in cursor.description]
    return [dict(zip(cols, fila)) for fila in cursor.fetchall()]


def _parse_json(valor):
    """certificaciones_requeridas viene como texto JSON desde MySQL."""
    if isinstance(valor, (bytes, bytearray)):
        valor = valor.decode("utf-8", "replace")
    if isinstance(valor, str):
        try:
            return json.loads(valor)
        except (ValueError, TypeError):
            return valor
    return valor


def _max_correlativo():
    """Mayor NNNN existente en codigos AYR-NNNN del espejo."""
    mx = 0
    for cod in Instalacion.objects.values_list("codigo", flat=True):
        m = re.match(r"^AYR-(\d+)$", cod or "")
        if m:
            mx = max(mx, int(m.group(1)))
    return mx


# ---------- lectura desde Ayres (solo SELECT) ----------
def _leer_ayres(terminos):
    """Devuelve (instalaciones, clientes) de Ayres.

    Filtro OPCIONAL por razon_social:
      - `terminos` con valores -> trae SOLO los clientes que matcheen (match
        tolerante a tildes/orden) y SUS instalaciones (WHERE cliente_id IN ...).
      - `terminos` VACÍO -> trae TODOS los clientes y TODAS las instalaciones
        (SELECT directo, sin IN gigante).
    """
    if "ayres" not in connections.databases:
        raise RuntimeError(
            "La conexion 'ayres' no esta configurada. El sync solo corre en el "
            "servidor (develop/prod) con AYRES_DATABASE_URL definida."
        )

    where, params = _prefiltro("razon_social", terminos)  # "" y [] si no hay terminos
    try:
        with connections["ayres"].cursor() as cur:
            cur.execute(f"SELECT * FROM clientes {where}", params)
            clientes = _filas(cur)

            if terminos:
                # Refine en Python (match tolerante) SOLO cuando hay términos. Sin
                # este guard, `_coincide(texto, [])` sería False para todos y se
                # sincronizarían 0 clientes.
                clientes = [c for c in clientes if _coincide(c.get("razon_social"), terminos)]
                cliente_ids = sorted({c["id"] for c in clientes if c.get("id")})
                instalaciones = []
                if cliente_ids:
                    marcas = ", ".join(["%s"] * len(cliente_ids))
                    cur.execute(
                        f"SELECT * FROM instalaciones WHERE cliente_id IN ({marcas})", cliente_ids
                    )
                    instalaciones = _filas(cur)
            else:
                # Sin filtro: TODOS los clientes ya vienen del SELECT de arriba y
                # TODAS las instalaciones en un SELECT directo (evita un IN enorme).
                cur.execute("SELECT * FROM instalaciones")
                instalaciones = _filas(cur)
    except RuntimeError:
        raise
    except Exception as e:  # error de conexion / SQL: mensaje claro
        raise RuntimeError(f"No se pudo leer de la BD de Ayres ('ayres'): {e}") from e

    return instalaciones, clientes


# ---------- sincronizacion (upsert al espejo) ----------
def sincronizar(dry_run=False, escribir=print):
    """Lee Ayres y hace upsert del espejo. Devuelve un dict resumen."""
    terminos = settings.SYNC_CLIENTES_RAZON
    if terminos:
        escribir(f"Clientes a sincronizar (razon_social): {terminos}")
    else:
        escribir("Sin filtro de razon_social: se sincronizan TODOS los clientes.")

    instalaciones, clientes = _leer_ayres(terminos)
    escribir(f"Ayres devolvio {len(clientes)} cliente(s) y {len(instalaciones)} instalacion(es).")

    resumen = {
        "clientes_creados": 0, "clientes_actualizados": 0,
        "instalaciones_creadas": 0, "instalaciones_actualizadas": 0,
        "dry_run": dry_run,
    }
    correlativo = _max_correlativo()

    contexto = nullcontext() if dry_run else transaction.atomic()
    with contexto:
        # --- Clientes ---
        for row in clientes:
            defaults = {f: row.get(f) for f in CLIENTE_FIELDS}
            if dry_run:
                existe = Cliente.objects.filter(id=row["id"]).exists()
                resumen["clientes_actualizados" if existe else "clientes_creados"] += 1
            else:
                _, creado = Cliente.objects.update_or_create(id=row["id"], defaults=defaults)
                resumen["clientes_creados" if creado else "clientes_actualizados"] += 1

        # --- Instalaciones ---
        for row in instalaciones:
            defaults = {f: row.get(f) for f in INSTALACION_FIELDS}
            defaults["certificaciones_requeridas"] = _parse_json(defaults.get("certificaciones_requeridas"))

            existente = Instalacion.objects.filter(id=row["id"]).only("id", "codigo").first()
            es_nueva = existente is None or not existente.codigo
            if es_nueva:
                correlativo += 1
                defaults["codigo"] = f"AYR-{correlativo:04d}"  # propio de Rondas, unico
            # Si ya existe con codigo: NO se toca (estable) -> no va en defaults.

            if dry_run:
                resumen["instalaciones_creadas" if es_nueva else "instalaciones_actualizadas"] += 1
                if es_nueva:
                    escribir(f"  [nueva] instalacion id={row['id']} -> codigo {defaults['codigo']}")
            else:
                _, creado = Instalacion.objects.update_or_create(id=row["id"], defaults=defaults)
                resumen["instalaciones_creadas" if creado else "instalaciones_actualizadas"] += 1

    prefijo = "[DRY-RUN] " if dry_run else ""
    escribir(
        f"{prefijo}Clientes: +{resumen['clientes_creados']} nuevos, "
        f"{resumen['clientes_actualizados']} actualizados. "
        f"Instalaciones: +{resumen['instalaciones_creadas']} nuevas, "
        f"{resumen['instalaciones_actualizadas']} actualizadas."
    )
    return resumen
