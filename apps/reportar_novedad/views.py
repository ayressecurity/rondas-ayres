"""
App 'reportar_novedad' — herramienta de PRUEBA (solo super_admin) para generar
eventos tipo 'novedad' en libro_novedades (alimentan el Informe de Novedades),
con foto adjunta en libro_novedades_media.

Opera dentro de la instalación seleccionada (instalacion_id en sesión).
"""
from uuid import uuid4

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction
from django.shortcuts import redirect, render
from django.utils import timezone

from apps.comun.decoradores import requiere_instalacion, solo_super_admin
from apps.novedades.models import LibroNovedades, LibroNovedadesMedia, TipoEvento, TipoMedia
from .forms import ReportarNovedadForm


@login_required
@requiere_instalacion
@solo_super_admin
def index(request):
    """Reporta una novedad: crea el evento + guarda en MEDIA la foto tomada con
    la cámara (llega como dataURL base64 ya validado en el form)."""
    if request.method == "POST":
        form = ReportarNovedadForm(request.POST)
        if form.is_valid():
            tipo = TipoEvento.objects.filter(codigo="novedad").first()
            if tipo is None:
                messages.error(request, "Falta el catálogo de eventos (corre seed_tipos_evento).")
            else:
                ahora = timezone.now()  # aware; se muestra en Santiago en los informes
                with transaction.atomic():
                    evento = LibroNovedades.objects.create(
                        instalacion_id=request.session["instalacion_id"],  # SIEMPRE de la sesión
                        guardia_keycloak_id=getattr(request.user, "keycloak_id", "") or "",
                        tipo_evento=tipo,
                        timestamp_evento=ahora,
                        timestamp_servidor=ahora,
                        estado="ok",
                        texto=form.cleaned_data["texto"],
                    )
                    # UN registro de media por CADA foto tomada.
                    for crudo, ext in form.imagenes:
                        path = default_storage.save(
                            f"novedades/{uuid4().hex}.{ext}", ContentFile(crudo)
                        )
                        LibroNovedadesMedia.objects.create(
                            libro_novedades=evento, tipo=TipoMedia.FOTO, path=path
                        )
                n = len(form.imagenes)
                messages.success(
                    request,
                    f"Novedad reportada con {n} foto{'s' if n != 1 else ''}.",
                )
                return redirect("reportar_novedad:index")  # PRG: limpia el formulario
    else:
        form = ReportarNovedadForm()
    return render(request, "reportar_novedad/index.html", {"form": form})
