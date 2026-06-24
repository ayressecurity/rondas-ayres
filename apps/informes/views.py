"""
Informes del libro de novedades (solo lectura). Cada vista reutiliza la base
común (apps/informes/base.py) y solo define su filtro de tipo_evento. La
exportación a Excel respeta el filtro de instalación Y el filtro de fechas.
"""
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.utils import timezone
from django.utils.text import slugify

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from apps.comun.decoradores import requiere_instalacion
from apps.novedades.models import CategoriaEvento
from .base import eventos_filtrados, render_informe


def _filtro_rondas(qs):
    """Todos los eventos MENOS los de categoría 'novedad'."""
    return qs.exclude(tipo_evento__categoria=CategoriaEvento.NOVEDAD)


def _filtro_novedades(qs):
    """ÚNICAMENTE eventos de categoría 'novedad'."""
    return qs.filter(tipo_evento__categoria=CategoriaEvento.NOVEDAD)


@login_required
@requiere_instalacion
def informe_rondas(request):
    return render_informe(
        request,
        titulo="Informe de Rondas",
        aplica_filtro=_filtro_rondas,
        export_url="informes:exportar_rondas",
    )


@login_required
@requiere_instalacion
def informe_novedades(request):
    return render_informe(
        request,
        titulo="Informe de Novedades",
        aplica_filtro=_filtro_novedades,
    )


# Color de marca (sin '#') para openpyxl.
ROJO_MARCA = "CC3333"
COLUMNAS = [
    ("Fecha/Hora", 20),
    ("Tipo de evento", 22),
    ("Punto de control", 22),
    ("Guardia", 24),
    ("Coordenadas", 30),
    ("Distancia (m)", 14),
    ("Geocerca", 12),
    ("Observación", 40),
]


def _geocerca_texto(ev):
    if ev.dentro_geocerca is True:
        return "Dentro"
    if ev.dentro_geocerca is False:
        return "Fuera"
    return "—"


@login_required
@requiere_instalacion
def exportar_rondas(request):
    """Exporta el Informe de Rondas a .xlsx respetando instalación + fechas."""
    eventos, _rango, etiqueta, _valores = eventos_filtrados(request, _filtro_rondas)
    instalacion = request.session.get("instalacion_nombre") or "instalacion"

    wb = Workbook()
    ws = wb.active
    ws.title = "Informe de Rondas"

    # Título (fila 1, combinada) con instalación y rango de fechas filtrado.
    n_cols = len(COLUMNAS)
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=n_cols)
    titulo = ws.cell(row=1, column=1, value=f"Informe de Rondas — {instalacion} — {etiqueta}")
    titulo.font = Font(bold=True, size=13, color=ROJO_MARCA)
    titulo.alignment = Alignment(horizontal="left", vertical="center")

    # Encabezados (fila 2): negrita, fondo rojo de marca, texto blanco.
    relleno = PatternFill(start_color=ROJO_MARCA, end_color=ROJO_MARCA, fill_type="solid")
    fuente = Font(bold=True, color="FFFFFF")
    centrado = Alignment(horizontal="center", vertical="center")
    fila_encabezado = 2
    for col, (nombre, ancho) in enumerate(COLUMNAS, start=1):
        celda = ws.cell(row=fila_encabezado, column=col, value=nombre)
        celda.fill = relleno
        celda.font = fuente
        celda.alignment = centrado
        ws.column_dimensions[get_column_letter(col)].width = ancho

    # Datos.
    fila = fila_encabezado + 1
    for ev in eventos:
        coords = "—"
        if ev.lat is not None and ev.lng is not None:
            coords = f"{ev.lat}, {ev.lng}"  # str del Decimal: punto decimal, completo
        distancia = float(ev.distancia_metros) if ev.distancia_metros is not None else "—"
        valores = [
            timezone.localtime(ev.timestamp_evento).strftime("%Y-%m-%d %H:%M:%S"),
            ev.tipo_evento.nombre,
            ev.punto_control.nombre if ev.punto_control else "—",
            ev.guardia_nombre,
            coords,
            distancia,
            _geocerca_texto(ev),
            ev.texto or "—",
        ]
        for col, valor in enumerate(valores, start=1):
            ws.cell(row=fila, column=col, value=valor)
        fila += 1

    ws.freeze_panes = "A3"  # fija título + encabezado al hacer scroll

    hoy = timezone.localtime(timezone.now()).date().isoformat()
    nombre_archivo = f"informe_rondas_{slugify(instalacion)}_{hoy}.xlsx"

    resp = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    resp["Content-Disposition"] = f'attachment; filename="{nombre_archivo}"'
    wb.save(resp)
    return resp
