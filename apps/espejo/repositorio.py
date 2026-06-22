"""
Repositorio del espejo: ÚNICA capa de acceso a clientes/instalaciones.

Las vistas SOLO llaman a estas funciones. Reemplazar simulado -> real = editar
SOLO este archivo (cambiar datos_demo por consultas a los modelos Cliente /
Instalacion de apps.espejo cuando Ayres360 vuelva). Las firmas no cambian.

Jerarquia: Cliente -> Instalacion (instalacion.cliente_id, logico SIN FK).
"""
from apps.espejo import datos_demo


# ---------- Clientes ----------
def listar_clientes():
    """Todos los clientes."""
    # // TEMPORAL: mañana = list(Cliente.objects.all())
    return list(datos_demo.CLIENTES_DEMO)


def obtener_cliente(cliente_id):
    """Un cliente o None."""
    # // TEMPORAL: mañana = Cliente.objects.filter(id=cliente_id).first()
    for cli in datos_demo.CLIENTES_DEMO:
        if cli["id"] == cliente_id:
            return cli
    return None


# ---------- Instalaciones ----------
def listar_instalaciones(cliente_id):
    """Instalaciones de un cliente (filtradas por cliente_id)."""
    # // TEMPORAL: mañana = list(Instalacion.objects.filter(cliente_id=cliente_id))
    return [ins for ins in datos_demo.INSTALACIONES_DEMO if ins["cliente_id"] == cliente_id]


def obtener_instalacion(instalacion_id):
    """Una instalación o None."""
    # // TEMPORAL: mañana = Instalacion.objects.filter(id=instalacion_id).first()
    for ins in datos_demo.INSTALACIONES_DEMO:
        if ins["id"] == instalacion_id:
            return ins
    return None
