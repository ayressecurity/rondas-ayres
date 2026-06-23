"""
Siembra el catálogo `tipo_evento` (módulo 7-8) con los eventos base.

Idempotente: usa update_or_create por `codigo` (campo unique), así que se puede
correr varias veces sin duplicar — si el código ya existe, actualiza nombre /
categoría / activo; si no, lo crea.

NO crea migraciones: el modelo TipoEvento ya existe. Esto solo carga datos.

Uso:  python manage.py seed_tipos_evento
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.novedades.models import CategoriaEvento, TipoEvento

# Catálogo base. categoria debe ser un valor del enum categoria_evento
# (sesion / ronda / novedad / error), respetado vía CategoriaEvento.
TIPOS_BASE = [
    {"codigo": "sesion_inicio", "nombre": "Inicio de sesión", "categoria": CategoriaEvento.SESION},
    {"codigo": "sesion_fin", "nombre": "Fin de sesión", "categoria": CategoriaEvento.SESION},
    {"codigo": "arribo", "nombre": "Arribo a punto", "categoria": CategoriaEvento.RONDA},
    {"codigo": "novedad", "nombre": "Novedad", "categoria": CategoriaEvento.NOVEDAD},
    {"codigo": "codigo_no_existe", "nombre": "Código no existe", "categoria": CategoriaEvento.ERROR},
]


class Command(BaseCommand):
    help = "Siembra el catálogo tipo_evento con los eventos base (idempotente)."

    @transaction.atomic
    def handle(self, *args, **options):
        creados = 0
        actualizados = 0

        for datos in TIPOS_BASE:
            _, creado = TipoEvento.objects.update_or_create(
                codigo=datos["codigo"],
                defaults={
                    "nombre": datos["nombre"],
                    "categoria": datos["categoria"],
                    "activo": True,
                },
            )
            if creado:
                creados += 1
                self.stdout.write(self.style.SUCCESS(f"  + creado    {datos['codigo']}"))
            else:
                actualizados += 1
                self.stdout.write(f"  · actualiza {datos['codigo']}")

        self.stdout.write(self.style.SUCCESS(
            f"\nListo: {creados} creado(s), {actualizados} actualizado(s). "
            f"Total en catálogo: {TipoEvento.objects.count()}."
        ))
