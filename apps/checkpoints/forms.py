"""
Formulario de PuntoControl (Nuevo / Editar). Como un FormRequest de Laravel:
valida y normaliza la entrada. NO toca instalacion_id ni qr_token (los pone la
vista): el formulario solo expone los campos editables por el usuario.

El checkbox se muestra como "No validar posición de arribo" = NOT validar_posicion,
así que se expone como campo propio `no_validar` y la vista lo invierte.
"""
from django import forms
from django.core.validators import MinValueValidator

from .models import PuntoControl


class PuntoControlForm(forms.ModelForm):
    # Campo propio (no del modelo): invierte validar_posicion para la UI.
    no_validar = forms.BooleanField(
        required=False,
        label="No validar posición de arribo",
        help_text="Si se marca, el arribo a este punto no exige estar dentro de la geocerca.",
    )
    # Foto opcional: se guarda en MEDIA y la vista setea foto_path.
    foto = forms.FileField(required=False, label="Foto")

    class Meta:
        model = PuntoControl
        # 'tipo' NO se expone: lo fija el backend SIEMPRE como "Codigo QR".
        fields = ["nombre", "observacion", "lat", "lng", "tolerancia_mts"]
        labels = {
            "nombre": "Nombre",
            "observacion": "Observación",
            "lat": "Latitud",
            "lng": "Longitud",
            "tolerancia_mts": "Tolerancia (mts)",
        }
        widgets = {
            "nombre": forms.TextInput(attrs={"maxlength": 120}),
            "observacion": forms.Textarea(attrs={"rows": 3}),
            "lat": forms.NumberInput(attrs={"step": "any"}),
            "lng": forms.NumberInput(attrs={"step": "any"}),
            "tolerancia_mts": forms.NumberInput(attrs={"min": 0, "step": 1}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # nombre es obligatorio; el resto opcional (coincide con el esquema).
        self.fields["nombre"].required = True
        self.fields["observacion"].required = False
        self.fields["lat"].required = True
        self.fields["lng"].required = True
        self.fields["tolerancia_mts"].required = True
        # Tolerancia entera >= 0.
        self.fields["tolerancia_mts"].validators.append(MinValueValidator(0))
        if not self.is_bound and self.instance.pk is None:
            # Alta: defaults. Tolerancia de esquema + posición por defecto
            # (sede/zona habitual de Las Condes) para no partir en (0, 0).
            self.fields["tolerancia_mts"].initial = 30
            self.fields["lat"].initial = "-33.359000"
            self.fields["lng"].initial = "-70.507100"

    def clean_nombre(self):
        nombre = (self.cleaned_data.get("nombre") or "").strip()
        if not nombre:
            raise forms.ValidationError("El nombre es obligatorio.")
        return nombre
