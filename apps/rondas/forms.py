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
from .models import Ronda, RepiteRecurrencia


class RondaForm(forms.ModelForm):
    MODO_ALEATORIO = "aleatoria"
    MODO_ESTIPULADA = "estipulada"
    MODO_CHOICES = [
        (MODO_ALEATORIO, "Ronda aleatoria"),
        (MODO_ESTIPULADA, "Ronda estipulada"),
    ]
    # 'nombre' es varchar en BD; aquí lo limitamos a un tipo de ronda fijo.
    NOMBRE_CHOICES = [
        ("Ronda Día", "Ronda Día"),
        ("Ronda Noche", "Ronda Noche"),
    ]

    nombre = forms.ChoiceField(choices=NOMBRE_CHOICES, label="Nombre")
    fecha_inicio = forms.DateField(
        label="Fecha de inicio",
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        input_formats=["%Y-%m-%d"],
    )
    # Rango horario del turno (manual). Puede cruzar medianoche (inicio > fin).
    hora_inicio = forms.TimeField(
        label="Hora inicio",
        widget=forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
        input_formats=["%H:%M", "%H:%M:%S"],
    )
    hora_fin = forms.TimeField(
        label="Hora fin",
        widget=forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
        input_formats=["%H:%M", "%H:%M:%S"],
    )
    # Opcional a nivel de campo: la obligatoriedad depende del modo (ver clean()).
    puntos = forms.ModelMultipleChoiceField(
        queryset=PuntoControl.objects.none(),  # se fija en __init__ por instalación
        widget=forms.CheckboxSelectMultiple,
        required=False,
        label="Puntos de control de la ronda",
    )
    # Orden de selección (estipulada): ids separados por coma, lo mantiene el JS.
    orden_ids = forms.CharField(required=False, widget=forms.HiddenInput)
    modo_orden = forms.ChoiceField(
        choices=MODO_CHOICES,
        widget=forms.RadioSelect,
        label="Modo de orden",
        initial=MODO_ALEATORIO,
    )
    # Sección 3 — programación (opcional). "" = sin programación.
    repite = forms.ChoiceField(
        choices=[("", "Sin programación")] + list(RepiteRecurrencia.choices),
        required=False,
        label="Repite",
    )

    class Meta:
        model = Ronda
        fields = ["nombre", "fecha_inicio", "hora_inicio", "hora_fin"]

    def __init__(self, *args, instalacion_id=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.horarios = []  # (hora, minuto) válidos; lo llena clean()

        qs = PuntoControl.objects.filter(activo=True)
        if instalacion_id is not None:
            qs = qs.filter(instalacion_id=instalacion_id)
        self.fields["puntos"].queryset = qs.order_by("nombre")

        # Edición: precargar selección, modo y orden desde la instancia/secuencia.
        if self.instance.pk and not self.is_bound:
            secuencia = self.instance.rondasecuencia_set.order_by("orden")
            self.fields["puntos"].initial = list(secuencia.values_list("punto_control_id", flat=True))
            self.fields["modo_orden"].initial = (
                self.MODO_ALEATORIO if self.instance.orden_aleatorio else self.MODO_ESTIPULADA
            )
            if not self.instance.orden_aleatorio:
                # Orden guardado de los checkpoints (para repintar la numeración).
                self.fields["orden_ids"].initial = ",".join(
                    str(i) for i in secuencia.values_list("punto_control_id", flat=True)
                )

    def clean(self):
        """Valida la selección de checkpoints (según modo) y los horarios (Sec. 3).

        - Ronda estipulada: exige ≥1 checkpoint marcado.
        - Ronda aleatoria: ignora la selección (usa todos los activos); exige que
          la instalación tenga al menos un checkpoint activo.
        - Programación opcional: solo se exige ≥1 horario válido si hay "repite".
        """
        cleaned = super().clean()

        modo = cleaned.get("modo_orden")
        if modo == self.MODO_ESTIPULADA:
            if not cleaned.get("puntos"):
                self.add_error("puntos", "Selecciona al menos un punto de control.")
        elif modo == self.MODO_ALEATORIO:
            if not self.fields["puntos"].queryset.exists():
                self.add_error(
                    None,
                    "La instalación no tiene puntos de control activos para una ronda aleatoria.",
                )

        repite = cleaned.get("repite") or ""

        # self.data es un QueryDict en peticiones reales (soporta getlist).
        getlist = getattr(self.data, "getlist", None)
        horas = getlist("hora") if getlist else []
        minutos = getlist("minuto") if getlist else []

        horarios = []
        for h, m in zip(horas, minutos):
            h, m = (h or "").strip(), (m or "").strip()
            if h == "" and m == "":
                continue  # fila vacía: se ignora
            try:
                hi, mi = int(h), int(m)
            except ValueError:
                self.add_error(None, "Cada horario debe tener hora y minuto numéricos.")
                continue
            if not (0 <= hi <= 23):
                self.add_error(None, f"Hora fuera de rango (0-23): {h}.")
            elif not (0 <= mi <= 59):
                self.add_error(None, f"Minuto fuera de rango (0-59): {m}.")
            else:
                horarios.append((hi, mi))

        if repite and not horarios:
            self.add_error(None, "Si defines una programación, agrega al menos un horario válido.")

        # Sin repite -> no hay programación (se descartan horarios).
        self.horarios = horarios if repite else []
        return cleaned

    @property
    def orden_aleatorio(self):
        """True si el modo elegido es aleatorio (para que la vista lo guarde)."""
        return self.cleaned_data.get("modo_orden") == self.MODO_ALEATORIO

    def puntos_ordenados(self):
        """PuntoControl seleccionados (estipulada) en el orden marcado por el usuario.

        Usa orden_ids (ids en orden de selección); cualquier seleccionado que no
        figure ahí se agrega al final. Solo considera puntos válidos del campo.
        """
        seleccion = list(self.cleaned_data.get("puntos") or [])
        por_id = {p.id: p for p in seleccion}
        ordenados, vistos = [], set()
        for s in (self.cleaned_data.get("orden_ids") or "").split(","):
            s = s.strip()
            if not s:
                continue
            try:
                pid = int(s)
            except ValueError:
                continue
            if pid in por_id and pid not in vistos:
                ordenados.append(por_id[pid])
                vistos.add(pid)
        for p in seleccion:
            if p.id not in vistos:
                ordenados.append(p)
                vistos.add(p.id)
        return ordenados
