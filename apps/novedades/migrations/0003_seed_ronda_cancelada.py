"""
Migración de DATOS (no de esquema): siembra el tipo_evento 'ronda_cancelada'
para que quede disponible en develop/prod al migrar (la app lo usa el endpoint
POST /api/rondas/<id>/cancelar).

Idempotente (update_or_create por 'codigo'): correr o revertir no duplica.
"""
from django.db import migrations


def sembrar_ronda_cancelada(apps, schema_editor):
    TipoEvento = apps.get_model("novedades", "TipoEvento")
    TipoEvento.objects.update_or_create(
        codigo="ronda_cancelada",
        defaults={"nombre": "Ronda cancelada", "categoria": "ronda", "activo": True},
    )


class Migration(migrations.Migration):

    dependencies = [
        ("novedades", "0002_alter_libronovedades_lat_alter_libronovedades_lng"),
    ]

    # Reverse = noop: no borramos datos al revertir (el catálogo es de referencia).
    operations = [
        migrations.RunPython(sembrar_ronda_cancelada, migrations.RunPython.noop),
    ]
