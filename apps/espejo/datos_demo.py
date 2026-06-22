"""
Datos SIMULADOS del espejo de Ayres360, con la FORMA REAL del esquema
(docs/rondas_schema.dbml: tablas cliente e instalacion, enum estado_av).

TEMPORAL: reemplazar por la capa espejo (modelos Cliente/Instalacion) cuando
Ayres360 vuelva. La vista NO usa esto directamente: pasa por repositorio.py.

Reglas del esquema respetadas:
- instalacion.cliente_id referencia al cliente por id (logico, SIN FK).
- id de cliente/instalacion = id de Ayres.
- estado usa el enum estado_av: "activo" | "inactivo".

Campos de DISPLAY (no existen en el esquema, solo para mostrar columnas):
- cliente["comuna"]      -> el esquema cliente NO tiene comuna.
- instalacion["telefono"] -> el esquema instalacion NO tiene telefono.
  Se agregan SOLO aqui para poder pintar las columnas pedidas; al pasar a la
  capa espejo real, resolver estas columnas como corresponda.
"""

# // TEMPORAL: reemplazar por la capa espejo cuando Ayres360 vuelva.
CLIENTES_DEMO = [
    {
        "id": 1, "razon_social": "Municipalidad de Lo Barnechea", "codigo_cc": "AYR-0001",
        "rut": "69.070.300-2", "telefono_contacto": "+56 2 2757 9000",
        "email_contacto": "contacto@lobarnechea.cl", "estado": "activo",
        "direccion": "Av. Raúl Labbé 13989",
        "comuna": "Lo Barnechea",  # display-only (no esta en el esquema cliente)
    },
    {
        "id": 2, "razon_social": "Corporación Casa Joven", "codigo_cc": "AYR-0002",
        "rut": "65.123.450-7", "telefono_contacto": "+56 2 2757 9100",
        "email_contacto": "contacto@casajoven.cl", "estado": "activo",
        "direccion": "Camino El Rodeo 12500",
        "comuna": "Lo Barnechea",  # display-only
    },
    {
        "id": 3, "razon_social": "Inmobiliaria Las Condes SpA", "codigo_cc": "AYR-0003",
        "rut": "76.543.210-K", "telefono_contacto": "+56 2 2345 6789",
        "email_contacto": "operaciones@inmolascondes.cl", "estado": "activo",
        "direccion": "Av. Apoquindo 4500",
        "comuna": "Las Condes",  # display-only
    },
    {
        "id": 4, "razon_social": "Retail Parque Arauco S.A.", "codigo_cc": "AYR-0004",
        "rut": "94.627.000-1", "telefono_contacto": "+56 2 2299 0500",
        "email_contacto": "seguridad@parquearauco.cl", "estado": "activo",
        "direccion": "Av. Kennedy 5413",
        "comuna": "Las Condes",  # display-only
    },
    {
        "id": 5, "razon_social": "Condominio Vista Cordillera", "codigo_cc": "AYR-0005",
        "rut": "53.318.920-4", "telefono_contacto": "+56 2 2811 2233",
        "email_contacto": "administracion@vistacordillera.cl", "estado": "inactivo",
        "direccion": "Camino La Pirámide 8100",
        "comuna": "Lo Barnechea",  # display-only
    },
    {
        "id": 6, "razon_social": "Clínica Alemana de Santiago", "codigo_cc": "AYR-0006",
        "rut": "70.886.300-8", "telefono_contacto": "+56 2 2210 1111",
        "email_contacto": "facilities@clinicalemana.cl", "estado": "activo",
        "direccion": "Av. Vitacura 5951",
        "comuna": "Vitacura",  # display-only
    },
]

# // TEMPORAL: reemplazar por la capa espejo cuando Ayres360 vuelva.
# Varias instalaciones por cliente, enlazadas por cliente_id (SIN FK).
# instalacion["codigo"]: // codigo propio de Rondas, simulado por ahora.
#   (NO viene de Ayres; secuencial y unico: AYR-0001, AYR-0002, ...)
INSTALACIONES_DEMO = [
    # Cliente 1 — Municipalidad de Lo Barnechea
    {
        "id": 101, "cliente_id": 1, "codigo": "AYR-0001", "nombre": "DIDECO", "categoria": "media",
        "direccion": "Av. Raúl Labbé 13989", "region": "Metropolitana",
        "comuna": "Lo Barnechea", "estado": "activo",
        "telefono": "+56 2 2757 9010",  # display-only (no esta en el esquema)
    },
    {
        "id": 102, "cliente_id": 1, "codigo": "AYR-0002", "nombre": "Edificio Consistorial", "categoria": "alta",
        "direccion": "Av. Raúl Labbé 12851", "region": "Metropolitana",
        "comuna": "Lo Barnechea", "estado": "activo",
        "telefono": "+56 2 2757 9020",  # display-only
    },
    # Cliente 2 — Corporación Casa Joven
    {
        "id": 201, "cliente_id": 2, "codigo": "AYR-0003", "nombre": "CASA JOVEN", "categoria": "media",
        "direccion": "Camino El Rodeo 12500", "region": "Metropolitana",
        "comuna": "Lo Barnechea", "estado": "activo",
        "telefono": "+56 2 2757 9110",  # display-only
    },
    # Cliente 3 — Inmobiliaria Las Condes SpA
    {
        "id": 301, "cliente_id": 3, "codigo": "AYR-0004", "nombre": "Edificio Apoquindo 4500", "categoria": "alta",
        "direccion": "Av. Apoquindo 4500", "region": "Metropolitana",
        "comuna": "Las Condes", "estado": "activo",
        "telefono": "+56 2 2345 6790",  # display-only
    },
    {
        "id": 302, "cliente_id": 3, "codigo": "AYR-0005", "nombre": "Torre Norte", "categoria": "alta",
        "direccion": "Av. Apoquindo 4501", "region": "Metropolitana",
        "comuna": "Las Condes", "estado": "inactivo",
        "telefono": "+56 2 2345 6791",  # display-only
    },
    # Cliente 4 — Retail Parque Arauco S.A.
    {
        "id": 401, "cliente_id": 4, "codigo": "AYR-0006", "nombre": "Mall Parque Arauco", "categoria": "alta",
        "direccion": "Av. Kennedy 5413", "region": "Metropolitana",
        "comuna": "Las Condes", "estado": "activo",
        "telefono": "+56 2 2299 0510",  # display-only
    },
    {
        "id": 402, "cliente_id": 4, "codigo": "AYR-0007", "nombre": "Estacionamientos Kennedy", "categoria": "media",
        "direccion": "Av. Kennedy 5500", "region": "Metropolitana",
        "comuna": "Las Condes", "estado": "activo",
        "telefono": "+56 2 2299 0520",  # display-only
    },
    # Cliente 5 — Condominio Vista Cordillera (cliente inactivo)
    {
        "id": 501, "cliente_id": 5, "codigo": "AYR-0008", "nombre": "Acceso Cordillera", "categoria": "baja",
        "direccion": "Camino La Pirámide 8100", "region": "Metropolitana",
        "comuna": "Lo Barnechea", "estado": "inactivo",
        "telefono": "+56 2 2811 2240",  # display-only
    },
    # Cliente 6 — Clínica Alemana de Santiago
    {
        "id": 601, "cliente_id": 6, "codigo": "AYR-0009", "nombre": "Acceso Vitacura", "categoria": "alta",
        "direccion": "Av. Vitacura 5951", "region": "Metropolitana",
        "comuna": "Vitacura", "estado": "activo",
        "telefono": "+56 2 2210 1120",  # display-only
    },
    {
        "id": 602, "cliente_id": 6, "codigo": "AYR-0010", "nombre": "Edificio B - Urgencias", "categoria": "alta",
        "direccion": "Av. Vitacura 5900", "region": "Metropolitana",
        "comuna": "Vitacura", "estado": "activo",
        "telefono": "+56 2 2210 1130",  # display-only
    },
]
