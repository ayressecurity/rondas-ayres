"""
Decoradores compartidos. Como los middleware/guards de rutas en Laravel.
Jerarquia de contexto: Cliente -> Instalacion -> Modulos.
Combinar con @login_required (estos asumen usuario ya autenticado).
"""
from functools import wraps

from django.shortcuts import redirect


def requiere_cliente(view):
    """Exige un cliente seleccionado. Si no hay, redirige a Clientes."""
    @wraps(view)
    def _wrapped(request, *args, **kwargs):
        if not request.session.get("cliente_id"):
            return redirect("clientes:index")
        return view(request, *args, **kwargs)
    return _wrapped


def requiere_instalacion(view):
    """
    Exige instalacion seleccionada (y, antes, cliente). Si falta la instalacion
    redirige a Instalaciones; si tampoco hay cliente, a Clientes. Se usa en los
    modulos que operan DENTRO de una instalacion.
    """
    @wraps(view)
    def _wrapped(request, *args, **kwargs):
        if not request.session.get("cliente_id"):
            return redirect("clientes:index")
        if not request.session.get("instalacion_id"):
            return redirect("instalaciones:index")
        return view(request, *args, **kwargs)
    return _wrapped
