"""
Campo propio 'qr' (secreto de enrolamiento) en instalacion. 100% aditivo:
arranca null en las instalaciones ya sincronizadas. unique + null permite
múltiples NULL. NO lo toca el sync (no está en INSTALACION_FIELDS).
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("espejo", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="instalacion",
            name="qr",
            field=models.CharField(blank=True, max_length=64, null=True, unique=True),
        ),
    ]
