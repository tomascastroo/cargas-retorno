# Fuentes de cargas

## Estado actual

El MVP **no integra ninguna fuente real**. No verificamos ninguna API pública y gratuita de
ofertas de carga en Argentina cuyo uso esté permitido para este fin. Las fuentes del MVP son:

| Código | Tipo | Contenido |
|---|---|---|
| `dadores-demo` | JSON (`data/dadores_demo.json`) | 18 ofertas sintéticas que cubren cada caso: compatibles, fuera de ruta, frío, capacidad, vencida, tomada, precio bajo, mercadería peligrosa, sin confirmar hace 60 h, y distintos comportamientos del dador simulado |
| `planilla-cooperativa-demo` | CSV (`data/planilla_cooperativa_demo.csv`) | 4 filas: un **duplicado** de una oferta del JSON escrito distinto, dos ofertas nuevas y un registro inválido |
| `socio-api-ejemplo` | HTTP JSON | Deshabilitada. Muestra cómo se integraría la API de un socio |

Las fechas de los datos demo son relativas (`pickup_in_days`, `valid_hours`, `seen_hours_ago`)
para que la demostración funcione cualquier día. El campo `simulation` sólo existe en los datos
demo y define cómo responde el dador simulado (`acepta`, `contraoferta` con `max_aumento_pct`,
`no_disponible`, `rechaza`).

Todos los nombres de dadores llevan "(demo)" y los contactos usan el dominio reservado
`.invalid`: no corresponden a empresas reales.

## Criterios para sumar fuentes reales

- Sólo con **permiso explícito** del titular de los datos (convenio con dadores de carga,
  cooperativas, acopios o plataformas que ofrezcan una API para terceros).
- **No** hacer scraping de sitios cuyos términos lo prohíban, **no** automatizar cuentas
  personales y **no** leer grupos privados de WhatsApp.
- Antes de usar una API "gratuita", verificar por escrito sus términos y límites (cantidad de
  requests, uso comercial, atribución) y documentarlos acá. Mantenerla opcional: si falla, la
  ingesta sigue con las demás fuentes.

## Cómo agregar una fuente

1. Si entrega JSON o CSV con el esquema `RawLoad` (ver `src/cargas/ingestion/normalize.py`),
   alcanza con registrar una fila en `load_sources` con `kind` = `json`, `csv` o `http` y su
   `config` (`file` o `url`).
2. Si tiene otro formato, crear un conector con `fetch(now) -> list[dict]` que traduzca al
   esquema común y sumarlo en `connector_for`.
3. Asignar una `reliability` (0 a 1), que pesa en el ranking.
4. Agregar un test con un conector estático (ver `tests/test_ingestion.py`).

Esquema mínimo de un registro:

```json
{
  "external_id": "id-en-la-fuente",
  "origin": "Córdoba", "destination": "Buenos Aires",
  "pickup_from": "2026-10-13", "pickup_to": "2026-10-14",
  "cargo_type": "general", "description": "Harina en bolsas",
  "pallets": 24, "weight_kg": 22000,
  "vehicle_types": ["semi"], "body_types": ["sider"],
  "requires_refrigeration": false,
  "price_ars": 1950000, "price_negotiable": true,
  "shipper_name": "Dador S.A.", "shipper_contact": "logistica@dador.com",
  "status": "available",
  "valid_until": "2026-10-15T18:00:00-03:00",
  "published_at": "2026-10-12T09:00:00-03:00"
}
```

Las localidades deben existir en el nomenclátor (`src/cargas/geo/places.py`). Si no existen, el
registro se rechaza con el motivo y conviene sumar la localidad.
