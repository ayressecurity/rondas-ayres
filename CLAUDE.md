# Rondas Web — Contexto del proyecto (leer siempre)

Sistema **Rondas**: web admin (Django) + futura app móvil Android (Kotlin) que reemplaza
**VigiControl** antes de **septiembre**. Empresa: **Ayres Security** (seguridad privada, Chile).
Dev: Vicente Gonzalez (viene de **Laravel** → al explicar, usar paralelismos con Laravel).
Este repo es el **Servidor 4 · Rondas** (`rondas.ayressecurity.cl`).

## 1. Arquitectura general (4 servidores)
- **Servidor 1 · Keycloak + Login** (`sso.ayressecurity.cl`): identidad, usuarios, roles, groups,
  sesiones, emisión de JWT, Google/Microsoft, SSO y pantalla de login. **YA OPERATIVO.**
- **Servidor 2 · Portal** (`portal.ayressecurity.cl`): Nginx + SPA. Puerta de entrada/router;
  lee `groups` del token y redirige a las apps. **YA OPERATIVO** (`portal-web`).
- **Servidor 3 · Ayres360** (`ayres-security.sistema.local`): Nginx + Django + MySQL.
  Comercial, RR.HH., operaciones, clientes, contratos. **Fuente de verdad** de clientes/instalaciones.
- **Servidor 4 · Rondas** (`rondas.ayressecurity.cl`): Nginx + Django + MySQL. Guardias, rondas,
  GPS, incidentes, asistencia + API REST para la app móvil. **← ESTE PROYECTO.**

## 2. Identidad / SSO (REGLA DURA)
- Keycloak es un **IAM único ya funcionando**: realm `ayres-security`, client scope `groups`
  con sus grupos, `portal-web` operativo, y el **token de acceso ya validado** (trae `sub`, `groups`, `roles`).
- **Hay un solo login (SSO).** El usuario inicia sesión UNA vez. **Rondas NO tiene login propio**:
  consume el token. NO hablar de "primer login" como si Rondas autenticara.
- **`sub`** = UUID único e inmutable del usuario → se guarda como **`keycloak_id`**.
  La relación entre sistemas es **lógica por UUID, nunca FK cruzada**. El alta local es JIT.
- **groups = qué se ve** (apps/enlaces). **roles = qué se puede hacer** (ver/crear/editar/eliminar).
- Roles operativos Rondas: Guardia / Supervisor / Administrador (mapeados sobre roles reales del realm).
- Flujo: usuario → Portal → Keycloak (login) → vuelve con tokens (PKCE) → la app valida el JWT (JWKS) → acceso.

## 3. Stack y entornos
- **Python 3.13** · Django 5.2.15 · DRF · mozilla-django-oidc · django-environ · whitenoise.
- **Local = SQLite** (sin instalar nada). **Servidor (develop/prod) = MySQL** vía `.env`.
- El motor se cambia SOLO en `DATABASE_URL` del `.env`; el código NO cambia.
- `mysqlclient` se comenta en local (Windows); en servidor va activo.

## 4. Convenciones
- **snake_case**: variables, funciones, roles, apps y archivos (ej. `control_vehicular`).
- **PascalCase**: clases y modelos (ej. `LibroNovedades`, `PuntoControl`, `Usuario`).
- `AUTH_USER_MODEL = "cuentas.Usuario"` (fijado antes de la 1ª migración).
- settings dividido: `config/settings/base.py` + `local.py` + `develop.py` + `prod.py`.
  `manage.py` usa `local`; `wsgi/asgi` usan `prod`.

## 5. Espejo de Ayres360 (CLAVE)
- `cliente` e `instalacion` son **tablas locales = espejo exacto de Ayres360** (alta/edición/borrado).
  Idénticas en local y prod, **SIN FK**, `id` = id de Ayres, **solo-lectura** desde la web.
- Código: `apps/espejo/models.py` (Cliente, Instalacion), `apps/espejo/sync.py` (upsert + borrado),
  `apps/espejo/management/commands/sync_ayres.py`, lector en `apps/comun/ayres.py`.
- Tiempo real: lo ideal es que **Ayres emita webhook/evento por cada CRUD** → `POST /interno/sync/ayres`.
  Fallback: polling del comando `sync_ayres`. (Coordinar con Ayres.)

## 6. Estructura del proyecto
```
manage.py             utilidad CLI (equivale a artisan)
CLAUDE.md · requirements.txt · .env · .env.example · .gitignore · db.sqlite3
docs/                 planificacion-rondas.docx · rondas_schema.dbml
config/settings/      base.py · local.py · develop.py · prod.py · __init__.py
config/               urls.py · wsgi.py · asgi.py · __init__.py
apps/cuentas          Usuario (keycloak_id, zona, turno) + auth_backend.py (vincula por sub)
apps/comun            base/dashboard + ayres.py (lector de Ayres)
apps/espejo           Cliente, Instalacion (réplica) + sync.py + management/commands/sync_ayres.py
apps/instalaciones    módulos 1-2-3 (leen del espejo; guardias por API a Ayres)
apps/checkpoints      módulo 4 — PuntoControl + QR + PDF
apps/rondas           módulos 5-6 — Ronda, RondaSecuencia, RondaGuardia, Programacion, ProgramacionHorario, Notificacion
apps/novedades        módulos 7-8 — TipoEvento, LibroNovedades, LibroNovedadesMedia + reportes/export
apps/control_vehicular Vehiculo (reemplaza el Google Form)
apps/personas         PENDIENTE (solo PK)
apps/dispositivos     PENDIENTE (solo PK)
apps/api              API móvil (DRF + JWT Keycloak)
templates/            base.html (layout: navbar + sidebar + bloque) + partials/ (navbar.html · sidebar.html)
static/               css/ · js/ · img/ (CSS/JS/imágenes globales)
media/                fotos/audio/video (dev); en prod object storage
```

## 7. Modelo de datos (resumen; esquema completo en docs/)
Capas: **espejo** (cliente, instalacion) · **catálogo** (tipo_evento) · **identidad** (usuario) ·
**configuración** (punto_control, ronda, ronda_secuencia, ronda_guardia, programacion,
programacion_horario, notificacion) · **ejecución** (libro_novedades, libro_novedades_media) ·
**control vehicular** (vehiculo).
- `libro_novedades` es la tabla caliente (alto volumen): índice (instalacion_id, timestamp_evento);
  `timestamp_evento` (terreno) + `timestamp_servidor` (offline). La leen los informes 7 y 8.
- `usuario` extiende AbstractUser: `keycloak_id` (UUID), `zona`, `turno`.
- `vehiculo` (sin FK): desplazamiento enum(entrada/salida), recinto, ppu varchar(30), kilometraje,
  tipo_vehiculo enum(motocicleta/furgon/auto/station_wagon/camioneta/mini_bus), nombre_conductor,
  codigo_conductor, turno enum(1er/2do/3er/intermedio/largo/especial), registrado_keycloak_id, creado_en.
- `persona` y `dispositivo`: PENDIENTES, **solo PK**.
- **FK reales SOLO entre tablas propias de Rondas.** Espejo, vehiculo, persona y dispositivo: SIN FK.
  Referencias al espejo y a Keycloak = lógicas (por id/UUID, indexadas).
- Catálogo de eventos: sesión_inicio/fin · arribo/partida (V04) · novedad (V03) · código_no_existe (_NE).
- Detalle exhaustivo (campos de cliente/instalacion, etc.): ver `docs/planificacion-rondas.docx` y `docs/rondas_schema.dbml`.

## 8. Endpoints
- API móvil (JWT): `GET /api/checkpoints/by-qr/{qr_token}` · `POST /api/eventos` ·
  `POST /api/eventos/{id}/media` · `GET /api/rondas?mias` · `GET /api/notificaciones?mias`.
- Lectura de Ayres (para el sync): instalaciones/clientes; `GET /api/colaboradores?instalacion={id}` (guardias en vivo).
- Interno: `POST /interno/sync/ayres` (aplica el cambio recibido desde Ayres al espejo).

## 9. Estado actual
- Esqueleto creado y validado: `config/settings/` split, `.env`, apps `cuentas` y `comun`,
  modelo `Usuario` propio migrado. Corre en local con SQLite.
- Client de prueba en Keycloak: `rondas-web-test` (confidencial), redirect `http://localhost:8000/oidc/callback/`.

## 10. Próximos pasos
1. Dashboard real (sidebar + navbar + plantillas base) — diseño profesional.
2. Enganchar el login SSO (botón) y probar con `rondas-web-test`.
3. App `espejo` + `sync_ayres`. Luego checkpoints, rondas, novedades, control_vehicular, API móvil.

## 11. Reglas duras (DO / DON'T)
- NO poner FK en espejo / vehiculo / persona / dispositivo.
- NO crear login propio: la autenticación es el SSO de Keycloak.
- NO migrar con el user por defecto (ya está `cuentas.Usuario`).
- Datos entre sistemas: por API o por UUID, jamás FK cruzada.
- `.env`, `.venv`, `db.sqlite3` van en `.gitignore` (no se suben).
- Respetar las convenciones de nombres de la sección 4.

## 12. Cómo se trabaja
Las decisiones de arquitectura se toman con el dev en el chat de planificación; este repo
**ejecuta**. Antes de cambios grandes, seguir lo definido aquí. Calidad estándar siempre:
código claro, comentado donde aporte, y consistente con la estructura existente.

## 13. Identidad de marca — usar SIEMPRE en la UI
Fuente: docs/MANUAL_DE_MARCA_AYRES.pdf. Toda interfaz (botones, navbar, sidebar, enlaces, estados, badges) debe respetar esto.

Colores (web):
- Rojo corporativo (primario): #CC3333 — acentos, botones primarios, enlaces/ítem activo, barras de marca.
- Oscuro corporativo: #333333 — texto principal, navbar/sidebar, fondos oscuros.
- Blanco: #FFFFFF — fondos y texto sobre oscuro.
- Predominan rojo + blanco; el #333 para texto y estructura. (Print: rojo Pantone B62025 / RGB 182,32,37; oscuro Pantone 1F1E1E. En web usar los HEX de arriba.)

Tipografía:
- Marca/títulos: Exo (cargar desde Google Fonts).
- Cuerpo/fallback: Arial, luego sans-serif (el manual permite Arial en web).
- Stack CSS: font-family: "Exo", Arial, sans-serif;

Logo (en static/img/):
- logo-ayres.png (versión color, para fondos claros) y logo-ayres-blanco.png (versión blanca, para fondos oscuros).
- Navbar: logo horizontal. Sobre fondo oscuro -> versión blanca; sobre claro -> color.
- No deformar, no recolorear, respetar un margen alrededor y tamaño legible.

Reglas de UI:
- Botón primario: fondo #CC3333, texto blanco; hover un poco más oscuro.
- Botón secundario: blanco con borde gris, texto #333.
- Estética profesional, limpia y seria (empresa de seguridad). Contraste y legibilidad siempre.
- Definir estos colores como variables CSS en static/css/dashboard.css (ej. --rojo:#CC3333; --oscuro:#333333) y reutilizarlas.
````
````