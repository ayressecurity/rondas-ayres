"""
App 'reportar_novedad' — herramienta de PRUEBA (solo super_admin) para generar
eventos tipo 'novedad' en libro_novedades (alimentan el Informe de Novedades),
con foto adjunta en libro_novedades_media.

Opera dentro de la instalación seleccionada (instalacion_id en sesión).
"""
from uuid import uuid4

from django.contrib import messages
from django.contrib.auth.decorators import login_required
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
    """Reporta una novedad: crea el evento + guarda la foto en MEDIA."""
    if request.method == "POST":
        form = ReportarNovedadForm(request.POST, request.FILES)
        if form.is_valid():
            tipo = TipoEvento.objects.filter(codigo="novedad").first()
            if tipo is None:
                messages.error(request, "Falta el catálogo de eventos (corre seed_tipos_evento).")
            else:
                ahora = timezone.now()  # aware; se muestra en Santiago en los informes
                foto = form.cleaned_data["foto"]
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
                    path = default_storage.save(f"novedades/{uuid4().hex}_{foto.name}", foto)
                    LibroNovedadesMedia.objects.create(
                        libro_novedades=evento, tipo=TipoMedia.FOTO, path=path
                    )
                messages.success(request, "Novedad reportada correctamente.")
                return redirect("reportar_novedad:index")  # PRG: limpia el formulario
    else:
        form = ReportarNovedadForm()
    return render(request, "reportar_novedad/index.html", {"form": form})
