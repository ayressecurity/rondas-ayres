"""
Vistas de instalaciones (nivel 2 del contexto). Lista FILTRADA por el cliente
seleccionado, seleccion por fila (guarda en sesion) y cambio.

Los datos vienen del repositorio del espejo (apps.espejo.repositorio): hoy
simulados, manana reales, sin tocar esta vista.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from apps.comun.decoradores import requiere_cliente
from apps.espejo import repositorio


@login_required
@requiere_cliente
def index(request):
    """Tabla de instalaciones del cliente seleccionado."""
    cliente_id = request.session["cliente_id"]
    instalaciones = repositorio.listar_instalaciones(cliente_id)
    return render(request, "instalaciones/index.html", {"instalaciones": instalaciones})


@login_required
@requiere_cliente
def seleccionar(request, instalacion_id):
    """Guarda la instalacion elegida en sesion y entra a su contexto."""
    ins = repositorio.obtener_instalacion(instalacion_id)
    # Debe existir Y pertenecer al cliente seleccionado en sesión (aislamiento):
    # si no, no se fija y se avisa (evita operar instalaciones de otro cliente).
    if not ins or ins.cliente_id != request.session.get("cliente_id"):
        messages.error(request, "Esa instalación no pertenece al cliente seleccionado.")
        return redirect("instalaciones:index")
    request.session["instalacion_id"] = ins.id
    request.session["instalacion_nombre"] = ins.nombre
    return redirect("comun:dashboard")


@login_required
def cambiar(request):
    """Limpia SOLO la instalacion y vuelve a la lista de instalaciones."""
    request.session.pop("instalacion_id", None)
    request.session.pop("instalacion_nombre", None)
    return redirect("instalaciones:index")
