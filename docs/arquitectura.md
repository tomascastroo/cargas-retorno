# Arquitectura

## Principios

- **Un orquestador, no un enjambre de agentes.** `Orchestrator` coordina parser, matching,
  agente comercial y respuesta en un flujo explícito. Cada módulo tiene una sola
  responsabilidad y se prueba por separado.
- **Determinístico primero.** El LLM sólo es una ayuda opcional para interpretar texto libre.
  Las decisiones (qué carga es compatible, cuánto se paga, qué se confirma) son reglas
  explícitas y auditables.
- **Sin procesos por agente.** La configuración de cada agente es una fila de `agents`. Cada
  mensaje se procesa en una transacción y el estado del diálogo vive en `conversations.state`.
- **Nada externo obligatorio.** Sin red, el sistema funciona con reglas, nomenclátor local,
  distancias estimadas y datos demo.

## Flujo de un mensaje

1. `POST /api/chat/messages` valida el token y abre una transacción con
   `app.company_id = <empresa del token>`.
2. El orquestador registra el mensaje entrante y mira la etapa de la conversación:
   - `idle` / `collecting`: el **parser** extrae un `TripDraft`, se combina con lo ya
     conocido, se elige el camión y, si falta un dato esencial (origen, destino o fecha), se
     pregunta **sólo** eso.
   - Con el viaje completo: se crea `trips`, el **matching** evalúa las cargas candidatas,
     se guardan los `matches` y se responden las tres mejores (etapa `choosing`).
   - `choosing`: número → **agente comercial**; `descartar N` → descarta; un viaje nuevo →
     nueva búsqueda.
   - `confirming`: `sí` → `bookings`; `no` → vuelve a las opciones.
3. Cada paso relevante escribe en `audit_events`.
4. La respuesta es una lista de `OutboundMessage` con texto plano (apto para WhatsApp) y un
   `payload` estructurado (tarjetas en la web, botones en WhatsApp en el futuro).

## Modelo de datos

| Tabla | Alcance | Contenido |
|---|---|---|
| `companies` | privada (RLS por `id`) | Empresa de transporte |
| `users` | privada | Usuarios, `phone` para el futuro canal WhatsApp |
| `agents` | privada, 1 por empresa | Tarifas, desvío, flexibilidad, exclusiones, rutas habituales, límites de negociación |
| `trucks` | privada | Patente, tipo, carrocería, capacidad, frío |
| `conversations` / `messages` | privada | Diálogo por canal y su estado |
| `trips` | privada | Viaje interpretado (texto original y método de parsing) |
| `matches` | privada | Puntaje, componentes, métricas, explicación y copia de la oferta |
| `negotiations` | privada | Piso, precio publicado, acordado, transcripción y canal |
| `bookings` | privada, 1 por viaje | Operación aprobada por el camionero |
| `audit_events` | privada, sólo inserción | Trazabilidad (un trigger impide UPDATE y DELETE) |
| `load_sources` | compartida | Fuentes, confiabilidad y estado de la última corrida |
| `loads` | compartida | Oportunidades normalizadas (huella única para deduplicar) |
| `load_source_refs` | compartida | Qué fuente publicó cada oferta, con qué id y precio |

Respecto de la lista inicial: los dadores de carga no tienen tabla propia (nombre y contacto
viven en la oferta, porque no los gestionamos); se agregó `messages` para el historial de la
conversación y `load_source_refs` para la trazabilidad de los duplicados.

## Aislamiento multiempresa

Dos barreras independientes:

1. **Aplicación**: todas las consultas filtran por `company_id` y los accesos por id usan
   `owned()`, que responde 404 (no 403, para no revelar que el recurso existe).
2. **Base de datos (Row-Level Security)**: migración `0002`. Cada tabla privada tiene
   `ENABLE` + `FORCE ROW LEVEL SECURITY` y la política
   `company_id = current_setting('app.company_id')::uuid`, para lectura y escritura. La
   aplicación fija esa variable con `set_config(..., true)`, que sólo dura la transacción y no
   contamina conexiones del pool.

Detalles:

- El rol de la aplicación **no** debe ser superusuario ni tener `BYPASSRLS` (ambos saltean RLS).
  `docker/init-db.sh` crea un rol así y la aplicación verifica al iniciar que el suyo lo sea
  (`assert_rls_safe_role`).
- El login necesita buscar al usuario por email antes de conocer su empresa: la política
  `auth_lookup` permite **sólo lectura** de `users` dentro de una transacción que active
  `app.auth_lookup`, y sólo el endpoint de login lo hace.
- RLS protege de errores de la aplicación (una consulta sin filtro), no de un atacante que
  ejecute SQL arbitrario con el rol de la aplicación, porque ese rol puede fijar las variables.
  Para eso aplican las defensas habituales: consultas parametrizadas (SQLAlchemy) y mínimos
  privilegios.
- `tests/test_rls.py` lo prueba directamente en la base: consultas sin `WHERE`, escritura con
  otro `company_id`, `UPDATE` cruzado y sin contexto de empresa.

## Ingesta

`ingestion/connectors.py` define conectores intercambiables (`fetch(now) → list[dict]`):
archivo JSON, CSV y HTTP JSON. `normalize.py` valida cada registro contra un esquema común
(`RawLoad`), resuelve las localidades y calcula una **huella** (origen, destino, fecha de retiro,
tipo, dador normalizado y tamaño; sin el precio). `service.py`:

- La misma fuente y el mismo `external_id` → actualización. Sólo la fuente **primaria** de una
  carga actualiza sus datos; las demás sólo confirman que sigue vigente.
- Otra fuente con la misma huella → duplicado: se agrega una referencia y no se crea otra carga.
- Registro inválido → se rechaza con su motivo y el resto continúa.
- Fuente caída → `last_status = error` y el resto continúa. Sus cargas siguen, pero pierden
  confiabilidad con el tiempo y a las 48 h se marcan como "requiere verificación".
- Al final, las ofertas con `valid_until` pasado se marcan `expired`.

Ejecutar: `python -m cargas.cli ingest` (en Compose: `docker compose exec app python -m cargas.cli ingest`).
Para periodicidad alcanza un cron del sistema: no hace falta un worker.

## Agente comercial

`commercial/agent.py` separa el **canal** (`ShipperChannel.request_quote`) de la **política**
(`negotiate`):

- Precio pedido inicial: `max(precio publicado, piso)`, donde `piso = tarifa mínima × km cargado`.
- Nunca acepta menos que el piso. Ante una contraoferta debajo del piso, repregunta (hasta
  `max_negotiation_rounds`) por un valor entre el piso y el tope
  `precio publicado × (1 + max_counter_uplift_pct)`.
- Si el tope no alcanza el piso, no contacta.
- El resultado es `awaiting_driver`: la reserva requiere el "sí" del camionero.

En el MVP el canal es `SimulatedShipperChannel`. Un canal real responderá `pending` y la
negociación se retomará cuando llegue la respuesta del dador. Recién ahí convendrá sumar un
worker.

## Distancias con OSRM (opcional, no probado contra un servidor real)

OSRM es open source y el extracto de Argentina de Geofabrik es gratuito (datos ODbL de
OpenStreetMap). Preparación (una vez; requiere unos 8 GB de RAM y algunos minutos):

```bash
mkdir osrm && cd osrm
curl -LO https://download.geofabrik.de/south-america/argentina-latest.osm.pbf
docker run --rm -v "$PWD:/data" ghcr.io/project-osrm/osrm-backend osrm-extract -p /opt/car.lua /data/argentina-latest.osm.pbf
docker run --rm -v "$PWD:/data" ghcr.io/project-osrm/osrm-backend osrm-partition /data/argentina-latest.osrm
docker run --rm -v "$PWD:/data" ghcr.io/project-osrm/osrm-backend osrm-customize /data/argentina-latest.osrm
docker run -d -p 5000:5000 -v "$PWD:/data" ghcr.io/project-osrm/osrm-backend osrm-routed --algorithm mld /data/argentina-latest.osrm
```

Después: `CARGAS_ROUTING_PROVIDER=osrm` y `CARGAS_OSRM_URL=http://host.docker.internal:5000`
(o la URL que corresponda). Si OSRM no responde, se usa la estimación y el match lo registra en
`metrics.distance_method`.

## Por qué no hay Redis, colas, LangGraph ni bases vectoriales

- Todo el procesamiento de un mensaje es sincrónico y tarda milisegundos (con LLM local, segundos).
- La ingesta se puede disparar con cron.
- El flujo es un diálogo con etapas fijas: no requiere planificación autónoma.
- No hay búsqueda semántica: las cargas se filtran con atributos estructurados.

Se justifican cuando lleguen la negociación real (respuestas asincrónicas), los avisos
proactivos o volúmenes que exijan procesamiento en segundo plano.
