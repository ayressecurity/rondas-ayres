"""
Informes del libro de novedades (solo lectura). Cada vista reutiliza
`render_informe` (apps/informes/base.py) y solo define su filtro de tipo_evento.
"""
from django.contrib.auth.decorators import login_required

from apps.comun.decoradores import requiere_instalacion
from apps.novedades.models import CategoriaEvento
from .base import render_informe


@login_required
@requiere_instalacion
def informe_rondas(request):
    """Todos los eventos MENOS los de categoría 'novedad' (arribo, sesión, error...)."""
    return render_informe(
        request,
        titulo="Informe de Rondas",
        aplica_filtro=lambda qs: qs.exclude(tipo_evento__categoria=CategoriaEvento.NOVEDAD),
    )


@login_required
@requiere_instalacion
def informe_novedades(request):
    """ÚNICAMENTE eventos de categoría 'novedad'. Misma base/tabla que Rondas."""
    return render_informe(
        request,
        titulo="Informe de Novedades",
        aplica_filtro=lambda qs: qs.filter(tipo_evento__categoria=CategoriaEvento.NOVEDAD),
    )
