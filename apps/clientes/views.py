"""
Vistas de clientes (nivel 1 del contexto). Se lista, se selecciona por fila
(guardando en sesion) y se cambia. Datos desde el repositorio del espejo.
"""
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from apps.espejo import repositorio


@login_required
def index(request):
    """Tabla de clientes para seleccionar por fila."""
    clientes = repositorio.listar_clientes()
    return render(request, "clientes/index.html", {"clientes": clientes})


@login_required
def seleccionar(request, cliente_id):
    """Guarda el cliente elegido, LIMPIA la instalacion y va a Instalaciones."""
    cli = repositorio.obtener_cliente(cliente_id)
    if not cli:
        return redirect("clientes:index")
    request.session["cliente_id"] = cli["id"]
    request.session["cliente_nombre"] = cli["razon_social"]
    # Cambiar de cliente invalida cualquier instalacion previa.
    request.session.pop("instalacion_id", None)
    request.session.pop("instalacion_nombre", None)
    return redirect("instalaciones:index")


@login_required
def cambiar(request):
    """Limpia cliente E instalacion y vuelve a la lista de clientes."""
    for clave in ("cliente_id", "cliente_nombre", "instalacion_id", "instalacion_nombre"):
        request.session.pop(clave, None)
    return redirect("clientes:index")
