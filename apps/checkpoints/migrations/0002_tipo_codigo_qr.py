# Fija el tipo de punto de control en "Codigo QR": nuevo default + backfill de
# los registros existentes (los que tengan tipo vacío o nulo).
from django.db import migrations, models


def poner_tipo_qr(apps, schema_editor):
    PuntoControl = apps.get_model("checkpoints", "PuntoControl")
    PuntoControl.objects.filter(tipo__isnull=True).update(tipo="Codigo QR")
    PuntoControl.objects.filter(tipo="").update(tipo="Codigo QR")


def revertir(apps, schema_editor):
    # No deshacemos los datos: dejar el tipo no rompe nada.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("checkpoints", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="puntocontrol",
            name="tipo",
            field=models.CharField(blank=True, default="Codigo QR", max_length=40, null=True),
        ),
        migrations.RunPython(poner_tipo_qr, revertir),
    ]
