"""
Renombra el 'nombre' de las rondas existentes de "Ronda Día/Noche" a
"Turno Día/Noche" (el campo describe el TURNO, no repite la palabra "Ronda").
Solo datos: no cambia el esquema. Reversible.
"""
from django.db import migrations

RENOMBRES = [
    ("Ronda Día", "Turno Día"),
    ("Ronda Noche", "Turno Noche"),
]


def a_turno(apps, schema_editor):
    Ronda = apps.get_model("rondas", "Ronda")
    for viejo, nuevo in RENOMBRES:
        Ronda.objects.filter(nombre=viejo).update(nombre=nuevo)


def a_ronda(apps, schema_editor):
    Ronda = apps.get_model("rondas", "Ronda")
    for viejo, nuevo in RENOMBRES:
        Ronda.objects.filter(nombre=nuevo).update(nombre=viejo)


class Migration(migrations.Migration):

    dependencies = [
        ("rondas", "0004_alter_programacionhorario_options_and_more"),
    ]

    operations = [
        migrations.RunPython(a_turno, a_ronda),
    ]
