"""
Middleware de AISLAMIENTO del rol 'cliente'.

Un usuario con rol 'cliente' (empresa externa, ej. Municipalidad de Las Condes)
queda AMARRADO al cliente que trae su token de Keycloak (claim 'cliente_id'). En
CADA request se fuerza request.session["cliente_id"] a ese valor, de modo que:

  - nunca puede elegir/forzar otro cliente (aunque manipule URLs o la sesion), y
  - si el claim no resuelve a un cliente vigente del espejo, se queda SIN contexto
    (no ve datos), jamas cae a "ver todos".

Es la defensa CENTRAL del aislamiento: corre siempre y corrige la sesion antes de
que las vistas o el menu la lean. NO toca a super_admin/sspp/cenapoc/guardias, que
eligen cliente manualmente (early-return). Debe ir DESPUES de Session y
Authentication middleware (usa request.session y request.user).
"""
from apps.cuentas import permisos
from apps.espejo import repositorio

# Claves de contexto que dependen del cliente elegido.
_CLAVES_CONTEXTO = ("cliente_id", "cliente_nombre", "instalacion_id", "instalacion_nombre")


class ForzarClienteMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        self._forzar(request)
        return self.get_response(request)

    def _forzar(self, request):
        user = getattr(request, "user", None)
        if not (user and user.is_authenticated):
            return  # anonimo o API (Bearer, sin sesion): no aplica

        # Solo el rol 'cliente' puro. super_admin y demas roles: intactos.
        if permisos.es_super_admin(request) or not permisos.es_cliente(request):
            return

        cid = permisos.cliente_de(request)
        cli = repositorio.obtener_cliente(cid) if cid else None

        if cli:
            # Fuerza (y corrige) el cliente del token. Solo escribe si cambia, para
            # no marcar la sesion como modificada en cada request. Si el cliente
            # cambia, invalida cualquier instalacion previa (contexto nuevo).
            if request.session.get("cliente_id") != cli.id:
                request.session["cliente_id"] = cli.id
                request.session["cliente_nombre"] = cli.razon_social
                request.session.pop("instalacion_id", None)
                request.session.pop("instalacion_nombre", None)
            else:
                # Cliente ya correcto: valida que la instalacion en sesion (si hay)
                # SIGA siendo de este cliente. Defensa TRANSVERSAL: cubre de una vez
                # todas las vistas @requiere_instalacion (checkpoints, control
                # vehicular, informes...) sin decorar cada una.
                self._sanear_instalacion(request, cli.id)
        else:
            # Claim ausente / no numerico / cliente borrado: SIN contexto. Se limpia
            # cualquier resto de sesion (nunca "ver todos"). pop() de una clave
            # ausente no marca la sesion como modificada.
            for clave in _CLAVES_CONTEXTO:
                request.session.pop(clave, None)

    @staticmethod
    def _sanear_instalacion(request, cliente_id):
        """Descarta la instalacion de sesion si no pertenece al cliente (una query
        indexada, solo cuando el cliente esta operando dentro de una instalacion)."""
        ins_id = request.session.get("instalacion_id")
        if not ins_id:
            return
        ins = repositorio.obtener_instalacion(ins_id)
        if not ins or ins.cliente_id != cliente_id:
            request.session.pop("instalacion_id", None)
            request.session.pop("instalacion_nombre", None)
