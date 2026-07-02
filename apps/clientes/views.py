"""
Vistas de clientes (nivel 1 del contexto). Se lista, se selecciona por fila
(guardando en sesion) y se cambia. Datos desde el repositorio del espejo.
"""
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import redirect, render

from apps.comun.decoradores import bloquear_rol_cliente
from apps.espejo import repositorio

POR_PAGINA = 15


@login_required
@bloquear_rol_cliente
def index(request):
    """Tabla de clientes (paginada de a 15) con buscador por razon social.

    El filtro va en el BACKEND (icontains sobre razon_social del queryset del
    espejo); el paginador se recalcula sobre el resultado filtrado. El termino `q`
    se conserva entre paginas (query_sin_page). La seleccion por fila no cambia.

    El rol 'cliente' NO ve la lista global (bloquear_rol_cliente lo redirige a sus
    instalaciones); su cliente ya lo fuerza el middleware.
    """
    q = (request.GET.get("q") or "").strip()
    clientes = repositorio.listar_clientes()
    if q:
        clientes = clientes.filter(razon_social__icontains=q)

    page_obj = Paginator(clientes, POR_PAGINA).get_page(request.GET.get("page"))
    # Querystring del filtro SIN 'page' (encodeado), para los enlaces del paginador.
    params = request.GET.copy()
    params.pop("page", None)
    query_sin_page = params.urlencode()
    return render(request, "clientes/index.html", {
        "page_obj": page_obj,
        "q": q,
        "query_sin_page": query_sin_page,
    })


@login_required
@bloquear_rol_cliente
def seleccionar(request, cliente_id):
    """Guarda el cliente elegido, LIMPIA la instalacion y va a Instalaciones.

    El rol 'cliente' no puede elegir cliente (bloquear_rol_cliente): su cliente lo
    fuerza el middleware. Para los demas roles, igual que siempre."""
    cli = repositorio.obtener_cliente(cliente_id)
    if not cli:
        return redirect("clientes:index")
    request.session["cliente_id"] = cli.id
    request.session["cliente_nombre"] = cli.razon_social
    # Cambiar de cliente invalida cualquier instalacion previa.
    request.session.pop("instalacion_id", None)
    request.session.pop("instalacion_nombre", None)
    return redirect("instalaciones:index")


@login_required
@bloquear_rol_cliente
def cambiar(request):
    """Limpia cliente E instalacion y vuelve a la lista de clientes.

    Bloqueado para el rol 'cliente' (romperia su contexto amarrado; el middleware
    lo re-forzaria igual, pero se bloquea explicito). Demas roles: igual."""
    for clave in ("cliente_id", "cliente_nombre", "instalacion_id", "instalacion_nombre"):
        request.session.pop(clave, None)
    return redirect("clientes:index")
