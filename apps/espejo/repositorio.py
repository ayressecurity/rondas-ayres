"""
Repositorio del espejo: ÚNICA capa de acceso a clientes/instalaciones.

Lee de las tablas espejo REALES (modelos Cliente / Instalacion), pobladas por
el sync desde Ayres360. Solo filas no borradas (deleted_at IS NULL). Las vistas
SOLO llaman a estas funciones.

Jerarquia: Cliente -> Instalacion (instalacion.cliente_id, logico SIN FK).
"""
from apps.espejo.models import Cliente, Instalacion


# ---------- Clientes ----------
def listar_clientes():
    """Clientes no borrados, ordenados por razon social."""
    return Cliente.objects.filter(deleted_at__isnull=True).order_by("razon_social")


def obtener_cliente(cliente_id):
    """Un cliente no borrado o None."""
    return Cliente.objects.filter(id=cliente_id, deleted_at__isnull=True).first()


# ---------- Instalaciones ----------
def listar_instalaciones(cliente_id):
    """Instalaciones no borradas de un cliente, ordenadas por codigo."""
    return Instalacion.objects.filter(
        cliente_id=cliente_id, deleted_at__isnull=True
    ).order_by("codigo")


def obtener_instalacion(instalacion_id):
    """Una instalación no borrada o None."""
    return Instalacion.objects.filter(id=instalacion_id, deleted_at__isnull=True).first()
