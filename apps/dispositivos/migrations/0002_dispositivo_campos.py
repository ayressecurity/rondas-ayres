"""
Completa la tabla 'dispositivo' (hasta ahora solo PK) con los campos del
enrolamiento. Todo AddField (aditivo). La tabla está vacía/sin uso, así que los
defaults de las columnas NOT NULL (instalacion_id, token_hash, nombre, creado_en)
son transitorios (preserve_default=False): solo sirven para crear la columna, NO
quedan en el estado del modelo y NO hay datos que migrar.
"""
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("dispositivos", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="dispositivo",
            name="instalacion_id",
            field=models.BigIntegerField(db_index=True, default=0),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="dispositivo",
            name="token_hash",
            field=models.CharField(db_index=True, default="", max_length=64, unique=True),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="dispositivo",
            name="nombre",
            field=models.CharField(blank=True, default="", max_length=120),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="dispositivo",
            name="activo",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="dispositivo",
            name="device_info",
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="dispositivo",
            name="last_seen",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="dispositivo",
            name="creado_en",
            field=models.DateTimeField(auto_now_add=True, default=django.utils.timezone.now),
            preserve_default=False,
        ),
    ]
