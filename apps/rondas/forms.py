"""
Formulario de Ronda (Nuevo / Editar) en una sola pantalla:
  - Sección 1: datos (nombre, fecha_inicio).
  - Sección 2: puntos de control de la ronda (checkboxes) + modo de orden (radio).

cliente_id / instalacion_id NO se exponen: los pone la vista desde la sesión.
La lista de puntos se restringe a los ACTIVOS de la instalación (seguridad: un
id de otra instalación no valida porque no está en el queryset del campo).
"""
from django import forms

from apps.checkpoints.models import PuntoControl
from .models import Ronda


class RondaForm(forms.ModelForm):
    MODO_ALEATORIO = "aleatorio"
    MODO_GUARDIA = "guardia"
    MODO_CHOICES = [
        (MODO_ALEATORIO, "El sistema define el orden (aleatorio)"),
        (MODO_GUARDIA, "El guardia elige el orden en terreno"),
    ]

    fecha_inicio = forms.DateField(
        label="Fecha de inicio",
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        input_formats=["%Y-%m-%d"],
    )
    puntos = forms.ModelMultipleChoiceField(
        queryset=PuntoControl.objects.none(),  # se fija en __init__ por instalación
        widget=forms.CheckboxSelectMultiple,
        label="Puntos de control de la ronda",
        error_messages={"required": "Selecciona al menos un punto de control."},
    )
    modo_orden = forms.ChoiceField(
        choices=MODO_CHOICES,
        widget=forms.RadioSelect,
        label="Modo de orden",
        initial=MODO_ALEATORIO,
    )

    class Meta:
        model = Ronda
        fields = ["nombre", "fecha_inicio"]
        labels = {"nombre": "Nombre"}
        widgets = {"nombre": forms.TextInput(attrs={"maxlength": 120})}

    def __init__(self, *args, instalacion_id=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["nombre"].required = True

        qs = PuntoControl.objects.filter(activo=True)
        if instalacion_id is not None:
            qs = qs.filter(instalacion_id=instalacion_id)
        self.fields["puntos"].queryset = qs.order_by("nombre")

        # Edición: precargar selección y modo desde la instancia/secuencia.
        if self.instance.pk and not self.is_bound:
            self.fields["puntos"].initial = list(
                self.instance.rondasecuencia_set.values_list("punto_control_id", flat=True)
            )
            self.fields["modo_orden"].initial = (
                self.MODO_ALEATORIO if self.instance.orden_aleatorio else self.MODO_GUARDIA
            )

    def clean_nombre(self):
        nombre = (self.cleaned_data.get("nombre") or "").strip()
        if not nombre:
            raise forms.ValidationError("El nombre es obligatorio.")
        return nombre

    @property
    def orden_aleatorio(self):
        """True si el modo elegido es aleatorio (para que la vista lo guarde)."""
        return self.cleaned_data.get("modo_orden") == self.MODO_ALEATORIO
