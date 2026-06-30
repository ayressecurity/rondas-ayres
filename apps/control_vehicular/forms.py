"""
Formulario de Control Vehicular: réplica EXACTA del Google Form "Registro
Vehicular Municipalidad de Las Condes" (mismos campos, opciones y orden).

Los enums se GUARDAN por VALOR (los del esquema: desplazamiento_vehiculo,
tipo_vehiculo, turno_vehiculo) y se MUESTRAN con la etiqueta del form original.
Las etiquetas se definen aquí (no en el modelo) para no alterar el modelo ni
generar migración. registrado_keycloak_id y creado_en NO van en el form: los pone
la vista (identidad del usuario logueado / hora del servidor).
"""
from django import forms
from django.core.validators import MinValueValidator

from .models import DesplazamientoVehiculo, TipoVehiculo, TurnoVehiculo, Vehiculo

# Recinto: DROPDOWN con las 10 opciones EXACTAS (en orden). Se guarda el texto.
RECINTOS = [
    "Los Tuliperos", "Bodega Municipal", "Edificio Decom", "Depto Transito",
    "Parques y Jardines", "Estadio Paul Harris", "Maquinaria y Aseo",
    "Club de Tenis", "Seguridad Publica", "Comunitario Padre Hurtado",
]

# Etiqueta visible (del form original) -> valor de enum (del esquema).
DESPLAZAMIENTO_CHOICES = [
    (DesplazamientoVehiculo.ENTRADA, "Entrada"),
    (DesplazamientoVehiculo.SALIDA, "Salida"),
]
TIPO_CHOICES = [
    (TipoVehiculo.MOTOCICLETA, "Motocicleta"),
    (TipoVehiculo.FURGON, "Furgon"),
    (TipoVehiculo.AUTO, "Auto"),
    (TipoVehiculo.STATION_WAGON, "Station Wagon"),
    (TipoVehiculo.CAMIONETA, "Camioneta"),
    (TipoVehiculo.MINI_BUS, "Mini bus"),
]
TURNO_CHOICES = [
    (TurnoVehiculo.PRIMER_TURNO, "1er Turno"),
    (TurnoVehiculo.SEGUNDO_TURNO, "2do Turno"),
    (TurnoVehiculo.TERCER_TURNO, "3er turno"),
    (TurnoVehiculo.INTERMEDIO, "intermedio"),
    (TurnoVehiculo.TURNO_LARGO, "Turno Largo"),
    (TurnoVehiculo.TURNO_ESPECIAL, "Turno Especial"),
]


class VehiculoForm(forms.ModelForm):
    # Los selects/radio usan las etiquetas del form original; el valor guardado es
    # el del enum del esquema.
    desplazamiento = forms.ChoiceField(
        choices=DESPLAZAMIENTO_CHOICES, label="Desplazamiento de Vehículo",
        widget=forms.RadioSelect,
    )
    recinto = forms.ChoiceField(
        choices=[("", "Selecciona un recinto")] + [(r, r) for r in RECINTOS],
        label="Recinto",
    )
    tipo_vehiculo = forms.ChoiceField(choices=TIPO_CHOICES, label="Tipo de Vehículo")
    turno = forms.ChoiceField(choices=TURNO_CHOICES, label="Turnos")

    class Meta:
        model = Vehiculo
        # El ORDEN de esta lista = el orden del Google Form (1..8).
        fields = [
            "desplazamiento", "recinto", "ppu", "kilometraje",
            "tipo_vehiculo", "nombre_conductor", "codigo_conductor", "turno",
        ]
        labels = {
            "ppu": "PPU (patente)",
            "kilometraje": "Registro de Kilómetros",
            "nombre_conductor": "Nombre del Conductor",
            "codigo_conductor": "Código de conductor",
        }
        widgets = {
            "ppu": forms.TextInput(attrs={"maxlength": 30, "placeholder": "Ej. ABCD12"}),
            "kilometraje": forms.NumberInput(attrs={"min": 0, "step": 1, "placeholder": "Ej. 15000"}),
            "nombre_conductor": forms.TextInput(attrs={"maxlength": 160, "placeholder": "Nombre y apellido"}),
            "codigo_conductor": forms.TextInput(attrs={"maxlength": 50, "placeholder": "Código interno"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # En el form original TODOS los campos son obligatorios (llevan *).
        for nombre in self.fields:
            self.fields[nombre].required = True
        # Kilometraje entero >= 0.
        self.fields["kilometraje"].validators.append(MinValueValidator(0))
