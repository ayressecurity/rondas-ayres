"""
Vistas de la API móvil (DRF, stateless).

La identidad SIEMPRE sale del token (request.auth_claims), JAMÁS del body.
El permiso por defecto es IsAuthenticated (ver REST_FRAMEWORK en settings).
"""
from rest_framework.decorators import api_view
from rest_framework.response import Response


@api_view(["GET"])
def me(request):
    """GET /api/me — eco de la identidad del token. Sirve para probar el portero.

    Requiere token válido. Devuelve el sub (CON guiones), los roles del token,
    email, nombre y si la fila local se acaba de crear (alta JIT).
    """
    claims = request.auth_claims
    nombre = f"{claims.get('given_name', '')} {claims.get('family_name', '')}".strip()
    return Response({
        "sub": request.sub_con_guiones,            # con guiones, como viene en el token
        "roles": request.token_roles,              # roles del realm (qué puede hacer)
        "email": claims.get("email"),
        "nombre": nombre or claims.get("preferred_username"),
        "creado_jit": request.creado_jit,          # True si la fila se creó en esta request
    })
