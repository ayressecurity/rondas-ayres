"""
Vistas de Control Vehicular: LISTA + ALTA. Reemplaza el Google Form de Registro
Vehicular. El módulo no filtra por instalación (Vehiculo no la guarda; el
"recinto" cumple ese rol), pero se opera dentro del contexto de instalación como
el resto de los módulos del sidebar (@requiere_instalacion).

registrado_keycloak_id = identidad del usuario logueado, CON guiones (NUNCA del
form). creado_en = hora del servidor (America/Santiago), automático.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import redirect, render

from apps.comun.decoradores import requiere_instalacion
from .forms import VehiculoForm
from .models import Vehiculo

POR_PAGINA = 20


@login_required
@requiere_instalacion
def index(request):
    """Lista de registros vehiculares, más reciente primero, paginada (20)."""
    qs = Vehiculo.objects.order_by("-creado_en", "-id")
    page_obj = Paginator(qs, POR_PAGINA).get_page(request.GET.get("page"))
    return render(request, "control_vehicular/index.html", {"page_obj": page_obj})


@login_required
@requiere_instalacion
def nuevo(request):
    """Alta de un registro vehicular (patrón PRG)."""
    if request.method == "POST":
        form = VehiculoForm(request.POST)
        if form.is_valid():
            vehiculo = form.save(commit=False)
            # Identidad SIEMPRE del usuario logueado (UUID -> str = CON guiones),
            # igual que el resto del sistema; nunca del form.
            vehiculo.registrado_keycloak_id = str(getattr(request.user, "keycloak_id", "") or "")
            vehiculo.save()  # creado_en se setea solo (auto_now_add)
            messages.success(request, f"Registro vehicular «{vehiculo.ppu}» guardado.")
            return redirect("control_vehicular:index")
    else:
        form = VehiculoForm()
    return render(request, "control_vehicular/form.html", {"form": form})
