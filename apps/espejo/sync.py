# Sincronizacion del espejo de Ayres360 (cliente, instalacion).
# TODO: implementar upsert + borrado del espejo a partir de los datos de Ayres
#       (lo ideal: webhook POST /interno/sync/ayres; fallback: comando sync_ayres).
# Sin FK: el 'id' local = id de Ayres, relacion logica por id/UUID.
