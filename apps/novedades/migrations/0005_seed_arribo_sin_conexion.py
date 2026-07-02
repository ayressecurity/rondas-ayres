"""
Migración de DATOS (no de esquema): siembra el tipo_evento 'arribo_sin_conexion'
para que quede disponible en develop/prod al migrar. Lo usa POST /api/eventos
cuando la app reenvía un escaneo hecho SIN señal (offline: true).

Idempotente (update_or_create por 'codigo'): correr o revertir no duplica.
"""
from django.db import migrations


def sembrar_arribo_sin_conexion(apps, schema_editor):
    TipoEvento = apps.get_model("novedades", "TipoEvento")
    TipoEvento.objects.update_or_create(
        codigo="arribo_sin_conexion",
        defaults={"nombre": "Arribo sin conexión", "categoria": "ronda", "activo": True},
    )


class Migration(migrations.Migration):

    dependencies = [
        ("novedades", "0004_libronovedades_comentario_central"),
    ]

    # Reverse = noop: no borramos datos al revertir (el catálogo es de referencia).
    operations = [
        migrations.RunPython(sembrar_arribo_sin_conexion, migrations.RunPython.noop),
    ]
