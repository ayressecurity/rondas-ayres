"""
Formulario de "Reportar novedad": una observación (obligatoria) + una foto
tomada con la cámara (getUserMedia) y enviada como dataURL base64 en un campo
oculto. Se valida que el contenido sea una imagen real (Pillow) y de formato
permitido (jpg/png/webp).
"""
import base64
import binascii
from io import BytesIO

from django import forms
from PIL import Image, UnidentifiedImageError

# Formato Pillow -> extensión de archivo.
FORMATOS_PERMITIDOS = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}


class ReportarNovedadForm(forms.Form):
    texto = forms.CharField(
        label="Observación",
        widget=forms.Textarea(attrs={"rows": 4, "maxlength": 2000,
                                     "placeholder": "Describe la novedad…"}),
    )
    # La foto llega como dataURL (data:image/jpeg;base64,...) desde el canvas.
    foto_data = forms.CharField(
        widget=forms.HiddenInput,
        error_messages={"required": "Debes tomar una foto antes de guardar."},
    )

    def clean_texto(self):
        texto = (self.cleaned_data.get("texto") or "").strip()
        if not texto:
            raise forms.ValidationError("La observación es obligatoria.")
        return texto

    def clean_foto_data(self):
        data = self.cleaned_data.get("foto_data") or ""
        if not (data.startswith("data:image/") and "," in data):
            raise forms.ValidationError("La foto no es válida. Tómala de nuevo.")
        _cabecera, b64 = data.split(",", 1)
        try:
            crudo = base64.b64decode(b64, validate=True)
        except (binascii.Error, ValueError):
            raise forms.ValidationError("La foto no se pudo procesar.")
        try:
            img = Image.open(BytesIO(crudo))
            img.verify()  # confirma que es una imagen real (no solo el prefijo)
        except (UnidentifiedImageError, OSError, ValueError):
            raise forms.ValidationError("El archivo no es una imagen válida.")
        formato = (img.format or "").upper()
        if formato not in FORMATOS_PERMITIDOS:
            raise forms.ValidationError("Formato no permitido (usa JPG, PNG o WEBP).")
        # Datos listos para que la vista guarde el archivo.
        self.imagen_bytes = crudo
        self.imagen_ext = FORMATOS_PERMITIDOS[formato]
        return data
