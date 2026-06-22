"""
Comando de sincronizacion del espejo de Ayres360 (fallback por polling).
Placeholder del esqueleto: aun sin logica. Uso: python manage.py sync_ayres
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Sincroniza el espejo de Ayres360 (cliente, instalacion)."

    def handle(self, *args, **options):
        self.stdout.write("sync pendiente")
