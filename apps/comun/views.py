"""Vistas comunes del proyecto (dashboard del esqueleto)."""
from django.contrib.auth.decorators import login_required
from django.shortcuts import render


@login_required
def dashboard(request):
    """Panel principal. Exige sesion SSO; muestra identidad + roles/groups.

    kc_roles, kc_grupos y es_super_admin llegan via context_processor,
    asi que no hace falta pasarlos aqui.
    """
    return render(request, "comun/dashboard.html")
