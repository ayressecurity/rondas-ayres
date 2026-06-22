"""
Comando de sincronizacion del espejo de Ayres360 (cliente, instalacion).
Uso:
    python manage.py sync_ayres            # aplica los cambios
    python manage.py sync_ayres --dry-run  # solo muestra que haria, sin escribir
Correr en el SERVIDOR (develop/prod), nunca en local (no hay conexion a Ayres).
"""
from django.core.management.base import BaseCommand, CommandError

from apps.espejo import sync


class Command(BaseCommand):
    help = "Sincroniza el espejo de Ayres360 (cliente, instalacion) por comuna."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Muestra que se crearia/actualizaria sin escribir en la BD.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        try:
            resumen = sync.sincronizar(dry_run=dry_run, escribir=self.stdout.write)
        except RuntimeError as e:
            raise CommandError(str(e))

        if dry_run:
            self.stdout.write(self.style.WARNING("Dry-run: no se escribio nada."))
        else:
            self.stdout.write(self.style.SUCCESS("Sincronizacion completada."))
        return None
