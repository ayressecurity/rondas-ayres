"""
Formulario de "Reportar novedad": una observación (obligatoria) + VARIAS fotos
tomadas con la cámara (getUserMedia). Las fotos llegan como un JSON con una lista
de dataURLs (data:image/...;base64,...) en un campo oculto. Se valida que cada
una sea una imagen real (Pillow) y de formato permitido (jpg/png/webp).
"""
import base64
import binascii
import json
from io import BytesIO

from django import forms
from PIL import Image, UnidentifiedImageError

# Formato Pillow -> extensión de archivo.
FORMATOS_PERMITIDOS = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}


class ReportarNovedadForm(forms.Form):
    MAX_FOTOS = 9  # tope razonable (grilla 3x3)

    texto = forms.CharField(
        label="Observación",
        widget=forms.Textarea(attrs={"rows": 4, "maxlength": 2000,
                                     "placeholder": "Describe la novedad…"}),
    )
    # Lista de fotos como JSON de dataURLs, llenada por el JS de la cámara.
    fotos_json = forms.CharField(
        widget=forms.HiddenInput,
        error_messages={"required": "Debes tomar al menos una foto antes de guardar."},
    )

    def clean_texto(self):
        texto = (self.cleaned_data.get("texto") or "").strip()
        if not texto:
            raise forms.ValidationError("La observación es obligatoria.")
        return texto

    def _validar_una(self, data):
        """Valida un dataURL y devuelve (bytes, extension)."""
        if not (isinstance(data, str) and data.startswith("data:image/") and "," in data):
            raise forms.ValidationError("Una de las fotos no es válida. Tómala de nuevo.")
        _cabecera, b64 = data.split(",", 1)
        try:
            crudo = base64.b64decode(b64, validate=True)
        except (binascii.Error, ValueError):
            raise forms.ValidationError("Una de las fotos no se pudo procesar.")
        try:
            img = Image.open(BytesIO(crudo))
            img.verify()  # confirma que es una imagen real
        except (UnidentifiedImageError, OSError, ValueError):
            raise forms.ValidationError("Una de las fotos no es una imagen válida.")
        formato = (img.format or "").upper()
        if formato not in FORMATOS_PERMITIDOS:
            raise forms.ValidationError("Formato no permitido (usa JPG, PNG o WEBP).")
        return crudo, FORMATOS_PERMITIDOS[formato]

    def clean_fotos_json(self):
        bruto = self.cleaned_data.get("fotos_json") or ""
        try:
            lista = json.loads(bruto)
        except (ValueError, TypeError):
            raise forms.ValidationError("No se recibieron las fotos correctamente.")
        if not isinstance(lista, list) or not lista:
            raise forms.ValidationError("Debes tomar al menos una foto antes de guardar.")
        if len(lista) > self.MAX_FOTOS:
            raise forms.ValidationError(f"Máximo {self.MAX_FOTOS} fotos por novedad.")
        # imagenes = lista de (bytes, ext); la usa la vista para guardar cada una.
        self.imagenes = [self._validar_una(d) for d in lista]
        return bruto
