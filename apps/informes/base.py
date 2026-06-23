"""
BASE COMÚN de los informes del libro de novedades (solo lectura).

Los informes (Rondas, Novedades, ...) comparten la MISMA estructura: listan
`libro_novedades` de la instalación seleccionada, ordenado de más reciente a más
antiguo. Lo único que cambia entre informes es el FILTRO por tipo_evento.

`render_informe` centraliza la consulta + el render; cada informe concreto solo
pasa su `titulo` y una función `aplica_filtro(qs) -> qs`.

Zona horaria: el template formatea timestamp_evento con el filtro `date`, que
convierte el datetime aware a la zona activa (TIME_ZONE = America/Santiago).
"""
from django.shortcuts import render

from apps.novedades.models import LibroNovedades


def render_informe(request, *, titulo, aplica_filtro):
    """Lista libro_novedades de la instalación en sesión aplicando `aplica_filtro`.

    Asume contexto de instalación garantizado por @requiere_instalacion en la
    vista que llama (instalacion_id presente en la sesión).
    """
    eventos = (
        LibroNovedades.objects
        .filter(instalacion_id=request.session["instalacion_id"])
        .select_related("tipo_evento", "punto_control")
        .order_by("-timestamp_evento")
    )
    eventos = aplica_filtro(eventos)
    return render(request, "informes/informe.html", {"titulo": titulo, "eventos": eventos})
