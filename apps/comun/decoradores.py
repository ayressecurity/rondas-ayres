"""
Decoradores compartidos. Como los middleware/guards de rutas en Laravel.
Jerarquia de contexto: Cliente -> Instalacion -> Modulos.
Combinar con @login_required (estos asumen usuario ya autenticado).
"""
from functools import wraps

from django.contrib import messages
from django.shortcuts import redirect

from apps.cuentas import permisos
from apps.espejo import repositorio


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


def _es_cliente_amarrado(request):
    """True si el usuario es rol 'cliente' puro (amarrado a su cliente del token).
    super_admin queda EXCLUIDO (elige cliente libremente, aunque llevara el rol)."""
    return permisos.es_cliente(request) and not permisos.es_super_admin(request)


def bloquear_rol_cliente(view):
    """El rol 'cliente' NO gestiona clientes: queda amarrado al suyo por el
    ForzarClienteMiddleware. Lo manda a sus instalaciones; los demas roles pasan
    igual que hoy. Se aplica a las vistas de seleccion/listado global de clientes."""
    @wraps(view)
    def _wrapped(request, *args, **kwargs):
        if _es_cliente_amarrado(request):
            return redirect("instalaciones:index")
        return view(request, *args, **kwargs)
    return _wrapped


def instalacion_del_cliente(view):
    """Defensa en profundidad para el rol 'cliente': la instalacion en sesion DEBE
    pertenecer a su cliente del token. Si no (sesion manipulada, o la instalacion
    cambio de cliente en Ayres), la descarta y redirige a Instalaciones. Los demas
    roles no se tocan. Va DESPUES de @requiere_instalacion (asume instalacion_id)."""
    @wraps(view)
    def _wrapped(request, *args, **kwargs):
        if _es_cliente_amarrado(request):
            ins_id = request.session.get("instalacion_id")
            ins = repositorio.obtener_instalacion(ins_id) if ins_id else None
            if not ins or ins.cliente_id != permisos.cliente_de(request):
                request.session.pop("instalacion_id", None)
                request.session.pop("instalacion_nombre", None)
                messages.error(request, "Esa instalación no pertenece a tu empresa.")
                return redirect("instalaciones:index")
        return view(request, *args, **kwargs)
    return _wrapped


def solo_super_admin(view):
    """
    Restringe la vista al rol 'super_admin' del token (permisos.es_super_admin).
    Si no lo es, redirige al dashboard con aviso. Para endpoints JSON conviene
    validar en línea y responder 403 (este decorador es para vistas de página).
    """
    @wraps(view)
    def _wrapped(request, *args, **kwargs):
        if not permisos.es_super_admin(request):
            messages.error(request, "Acceso restringido a super administradores.")
            return redirect("comun:dashboard")
        return view(request, *args, **kwargs)
    return _wrapped
