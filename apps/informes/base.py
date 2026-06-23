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
from django.contrib.auth import get_user_model
from django.shortcuts import render

from apps.novedades.models import LibroNovedades


def _nombres_de_guardias(eventos):
    """Mapa keycloak_id -> "first_name last_name" para los guardias de `eventos`.

    Precarga en UNA sola consulta (evita N+1). Solo incluye al usuario si tiene
    nombre; el fallback al UUID se resuelve por fila al asignar guardia_nombre.
    """
    ids = {ev.guardia_keycloak_id for ev in eventos if ev.guardia_keycloak_id}
    if not ids:
        return {}
    Usuario = get_user_model()
    nombres = {}
    for u in Usuario.objects.filter(keycloak_id__in=ids).values_list(
        "keycloak_id", "first_name", "last_name"
    ):
        keycloak_id, first, last = u
        nombre = f"{first or ''} {last or ''}".strip()
        if nombre:
            nombres[keycloak_id] = nombre
    return nombres


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
    eventos = list(aplica_filtro(eventos))

    # Nombre del guardia desde cuentas.Usuario (por keycloak_id); fallback al UUID.
    nombres = _nombres_de_guardias(eventos)
    for ev in eventos:
        ev.guardia_nombre = nombres.get(ev.guardia_keycloak_id) or ev.guardia_keycloak_id or "—"

    return render(request, "informes/informe.html", {"titulo": titulo, "eventos": eventos})
