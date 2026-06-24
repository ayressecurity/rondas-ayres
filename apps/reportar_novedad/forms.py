"""
Formulario de "Reportar novedad": una observación (obligatoria) + una foto
(solo imágenes jpg/png/webp). ImageField valida que sea una imagen real (Pillow);
el FileExtensionValidator restringe el formato.
"""
from django import forms
from django.core.validators import FileExtensionValidator

EXTENSIONES_FOTO = ["jpg", "jpeg", "png", "webp"]


class ReportarNovedadForm(forms.Form):
    texto = forms.CharField(
        label="Observación",
        widget=forms.Textarea(attrs={"rows": 4, "maxlength": 2000,
                                     "placeholder": "Describe la novedad…"}),
    )
    foto = forms.ImageField(
        label="Foto",
        validators=[FileExtensionValidator(EXTENSIONES_FOTO)],
        widget=forms.ClearableFileInput(attrs={"accept": "image/jpeg,image/png,image/webp"}),
        help_text="Solo imágenes: JPG, PNG o WEBP.",
    )

    def clean_texto(self):
        texto = (self.cleaned_data.get("texto") or "").strip()
        if not texto:
            raise forms.ValidationError("La observación es obligatoria.")
        return texto
