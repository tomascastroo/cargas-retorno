# Cargas de retorno

Agente que consigue **carga para el viaje de vuelta** de un camión. El camionero escribe
*"Vuelvo mañana de Córdoba a Buenos Aires vacío"* y el sistema interpreta el viaje, busca
oportunidades compatibles, las ordena con un puntaje explicable, contacta al dador (simulado en
el MVP) y registra la operación cuando el camionero la aprueba.

Plataforma **multiempresa**: cada empresa de transporte tiene sus camiones, su agenda de
dadores de carga, la configuración de su agente, sus conversaciones y su historial, aislados con `company_id` + Row-Level Security de
PostgreSQL. Las oportunidades de carga son compartidas.

**Costo de ejecución: cero.** Todo corre localmente con software open source: Python, FastAPI,
PostgreSQL y, opcionalmente, Ollama. No usa APIs pagas ni servicios con tarjeta.

---

## Inicio rápido (Docker Compose)

Requisitos: Docker con Compose v2.

```bash
docker compose up -d --build
```

Abrí **http://localhost:8000**. Al iniciar, el servicio `app`:

1. aplica las migraciones (`alembic upgrade head`),
2. registra las fuentes demo y corre la ingesta de cargas,
3. crea dos empresas demo (idempotente: no duplica nada al reiniciar).

| Usuario | Empresa | Contraseña |
|---|---|---|
| `admin@transportesdelcentro.demo` | Transportes del Centro (demo): semi sider AB123CD, 28 pallets, excluye mercadería peligrosa, 4 contactos de carga | `demo1234` |
| `admin@logisticasur.demo` | Logística Sur (demo): semi térmico AC456EF, 26 pallets | `demo1234` |

Otros comandos:

```bash
docker compose logs -f app                       # ver migraciones, ingesta y requests
docker compose exec app python -m cargas.cli ingest   # volver a consultar las fuentes
docker compose down -v                           # borrar todo (incluida la base)
```

La base queda publicada en el puerto **5433** del host (no en 5432, para no chocar con un
PostgreSQL local). Si el 8000 o el 5433 están ocupados: `APP_PORT=8080 DB_PORT=5434 docker compose up -d`.

API interactiva (OpenAPI): http://localhost:8000/docs

## Demostración guiada (escenario del MVP)

En la interfaz web (o con la API, ver más abajo):

1. **Crear una empresa**: en la pantalla de acceso, *Crear una empresa de transporte*.
2. **Registrar un semi de 28 pallets**: *Configuración → Camiones*.
3. **Oportunidades de demostración**: ya cargadas por la ingesta (ver *Historial → Fuentes*).
4. **Mensaje**: en *Chat*, escribí `Vuelvo mañana de Córdoba a Buenos Aires vacío`.
5. **Interpretación**: el agente informa origen, destino, fecha, camión y pallets libres.
6. **Búsqueda y ranking**: revisa todas las ofertas, descarta las incompatibles y dice cuántas
   y por qué (fuera de ruta, vencidas, sin capacidad, requieren frío, etc.).
7. **Mejores opciones**: tres tarjetas con origen, destino, mercadería, precio, puntaje, sus
   cinco componentes y el motivo de la recomendación.
8. **Elegir** (`1` o el botón *Elegir*): el agente "contacta" al dador. Es una **simulación**:
   se ve la transcripción y no se envía nada real.
9. **Confirmar** (`sí`): se registra la operación (`simulated_confirmed`). En *Historial* quedan
   el viaje, la operación y la auditoría.
10. **Aislamiento**: salí, entrá con otra empresa y verificá que no ve nada de la primera.

Casos para probar en el chat:

| Mensaje | Qué muestra |
|---|---|
| `Vuelvo a Buenos Aires` | Pide **sólo** lo que falta (origen y fecha) |
| `hola` | Error de interpretación con un ejemplo |
| `Vuelvo mañana de Comodoro Rivadavia a Trelew` | Sin resultados, con los motivos |
| `descartar 1` | Descarta una opción y muestra la siguiente |
| Elegir *Repuestos de maquinaria agrícola* | El dador responde "ya no está disponible" y se muestran las restantes |
| Elegir *Leche en polvo* | Contraoferta del dador dentro de los límites; responder `no` la descarta |
| `nuevo` | Reinicia la conversación |

**Contactos de carga**: en *Configuración → Contactos de carga* se cargan los dadores con los que
trabaja la empresa, a mano o importando una planilla CSV (hay una de ejemplo para descargar).
En cada búsqueda, el agente sugiere los que cargan en el origen o sobre la ruta. Ver
[docs/contactos.md](docs/contactos.md).

El escenario completo también está automatizado en `tests/test_flow.py::test_full_scenario`.

## Qué es real y qué está simulado

| Componente | Estado en el MVP |
|---|---|
| Interpretación de mensajes (reglas) | **Real** y probada. Español rioplatense: ciudades con alias ("bs as", "cba"), hoy/mañana/pasado mañana, días de la semana, dd/mm, semi/chasis/balancín, pallets, toneladas, patentes, "vacío" |
| Interpretación con LLM (Ollama) | **Real pero opcional y no probada con un modelo real** en el entorno de desarrollo (la descarga de modelos estaba bloqueada por la red). Probada con un servidor HTTP simulado: salida validada con Pydantic y fallback a reglas ante cualquier error |
| Nomenclátor de localidades | **Real**, local: 48 localidades con coordenadas aproximadas de OpenStreetMap |
| Distancias | **Estimadas**: línea recta × 1,12 (±10-15 % frente a la ruta real). Adaptador OSRM implementado (servidor propio, gratuito); probado con un servidor simulado, **no** con un OSRM real |
| Ingesta (normalización, duplicados, vencimientos, fuentes que fallan) | **Real** y probada |
| Fuentes de cargas | **Datos de demostración** (JSON + CSV con fechas relativas, para que la demo funcione cualquier día). No hay fuentes reales integradas: ver [docs/fuentes.md](docs/fuentes.md) |
| Matching y ranking | **Real**, determinístico y probado ([docs/matching.md](docs/matching.md)) |
| Contacto y negociación con el dador | **Simulada**: el dador demo responde según el campo `simulation` de cada carga. La política de negociación (tarifa mínima, rondas, tope de contraoferta) es real y está probada |
| Reserva | **Real como registro**, con aprobación explícita del camionero; estado `simulated_confirmed` porque no se confirma nada con el dador real |
| Multiempresa / RLS / auditoría inmutable | **Real** y probado contra PostgreSQL |
| Contactos de carga (agenda, CSV, sugerencias por ruta) | **Real** y probado. El agente los **sugiere**; todavía no les escribe |
| Canal | Chat web que imita WhatsApp. **No** hay integración con WhatsApp |
| Autenticación | Tokens firmados (HMAC) y contraseñas con scrypt; suficiente para el MVP, sin recuperación de contraseña ni roles finos |

## Arquitectura (resumen)

```
             ┌──────────── Canal (chat web hoy · WhatsApp mañana) ────────────┐
             │   POST /api/chat/messages → InboundMessage / OutboundMessage   │
             └───────────────────────────────┬────────────────────────────────┘
                                             ▼
                         Orquestador (estado en conversations.state)
     ┌───────────────┬───────────────────────┼─────────────────────┬──────────────────┐
     ▼               ▼                       ▼                     ▼                  ▼
  Parser      Matching Engine        Agente comercial          Respuesta         Auditoría
  reglas +    elegibilidad →         canal ShipperChannel      opciones +        audit_events
  Ollama opc. ranking 0-100          (simulado) + política     explicación       (sólo inserción)
                     ▲
                     │ loads (compartidas)
              Ingesta: conectores JSON / CSV / HTTP → normalización → duplicados → vencimiento
```

Un solo proceso FastAPI y un PostgreSQL. Sin colas, Redis, microservicios, LangGraph ni bases
vectoriales: el volumen del MVP no los necesita. La configuración de cada agente es una fila
(`agents`), no un proceso. Detalle en [docs/arquitectura.md](docs/arquitectura.md).

## Estructura

```
src/cargas/
  main.py              FastAPI + interfaz web estática
  orchestrator.py      flujo de la conversación (único punto que coordina todo)
  parser/              reglas (rules.py), LLM opcional + validación (service.py), esquemas
  matching/engine.py   elegibilidad, puntaje y explicación (funciones puras)
  commercial/agent.py  ShipperChannel, dador simulado y política de negociación
  ingestion/           conectores, normalización + huella de duplicados, servicio de ingesta
  geo/                 nomenclátor local y distancias (haversine / OSRM)
  channels/base.py     mensajes independientes del canal
  api/                 endpoints, esquemas, autenticación y contexto de empresa
  models.py, db.py     SQLAlchemy y sesiones con contexto RLS
  contacts.py          agenda de dadores: validación, importación CSV y sugerencias por ruta
  cli.py               migrate · seed · ingest
migrations/            Alembic (0001 esquema, 0002 RLS + auditoría inmutable, 0003 contactos)
data/                  cargas demo (JSON y CSV)
tests/                 pytest contra PostgreSQL real
docker/init-db.sh      crea el rol de la aplicación SIN superusuario ni BYPASSRLS
```

## Desarrollo sin Docker

Requisitos: Python 3.12+, [uv](https://docs.astral.sh/uv/) y PostgreSQL 14+.

```bash
# Rol de la aplicación: NO superusuario (si no, RLS no se aplica y la app se niega a iniciar)
sudo -u postgres psql -c "CREATE ROLE cargas LOGIN PASSWORD 'cargas' CREATEDB;"
sudo -u postgres createdb -O cargas cargas
sudo -u postgres createdb -O cargas cargas_test

uv sync
export CARGAS_DATABASE_URL=postgresql+psycopg://cargas:cargas@localhost:5432/cargas
uv run python -m cargas.cli migrate
uv run python -m cargas.cli seed
uv run uvicorn cargas.main:app --reload
```

O sólo la base con Docker: `docker compose up -d db` (el rol `cargas` tiene contraseña
`cargas_dev_password` y crea también `cargas_test`).

## Tests

```bash
uv run pytest            # usa postgresql://cargas:cargas@localhost:5432/cargas_test
# con la base de Docker Compose:
CARGAS_TEST_DATABASE_URL=postgresql+psycopg://cargas:cargas_dev_password@localhost:5433/cargas_test uv run pytest
uv run ruff check . && uv run ruff format --check .
```

86 tests: parser (incluido el fallback del LLM), matching (cada regla de elegibilidad y el
ranking), contactos (validación, CSV, aislamiento, sugerencias), ingesta (duplicados, rechazos, vencimientos, fuente caída), negociación (nunca debajo
del mínimo), escenario completo por API, aislamiento por API y **RLS directo en la base**
(consultas sin filtro, escritura cruzada, auditoría inmutable).

Los tests recrean el esquema de la base de test en cada corrida.

## Configuración

Variables con prefijo `CARGAS_` (ver `.env.example` y `src/cargas/config.py`):

| Variable | Defecto | Uso |
|---|---|---|
| `CARGAS_DATABASE_URL` | local | Conexión con el rol de la aplicación |
| `CARGAS_SECRET_KEY` | valor de desarrollo | Firma de tokens. Obligatorio cambiarlo en producción (la app se niega a iniciar) |
| `CARGAS_LLM_PROVIDER` | `none` | `ollama` para usar el LLM local |
| `CARGAS_OLLAMA_URL` / `CARGAS_OLLAMA_MODEL` | `http://localhost:11434` / `qwen2.5:1.5b` | |
| `CARGAS_ROUTING_PROVIDER` | `haversine` | `osrm` con un servidor OSRM propio |
| `CARGAS_TIMEZONE` | `America/Argentina/Buenos_Aires` | Para "hoy" y "mañana" |

Por empresa, en *Configuración → Agente*: tarifa mínima, tarifa objetivo y costo por km, desvío
máximo, días de flexibilidad, mercaderías excluidas, rondas y tope de contraoferta.

### LLM local (opcional)

```bash
docker compose --profile llm up -d
docker compose exec ollama ollama pull qwen2.5:1.5b   # ~1 GB, corre en CPU con 4 GB de RAM libres
CARGAS_LLM_PROVIDER=ollama docker compose up -d app
```

Ollama es open source (MIT) y Qwen2.5 1.5B tiene licencia Apache-2.0. El LLM sólo **extrae
campos**: lo que devuelve se valida (esquema Pydantic, localidad existente en el nomenclátor,
fecha no pasada) y lo que no valida se completa con las reglas. Si Ollama no responde, el sistema
sigue funcionando con las reglas y lo registra (`parse_method = rules`). Con poco hardware,
dejá `CARGAS_LLM_PROVIDER=none`: las reglas cubren los mensajes típicos.

## Endpoints principales

| Método | Ruta | Descripción |
|---|---|---|
| POST | `/api/companies` | Alta de empresa + administrador (devuelve token) |
| POST | `/api/auth/login` | Login |
| GET/POST/DELETE | `/api/trucks` | Camiones de la empresa |
| GET/PUT | `/api/agent` | Configuración del agente |
| GET/POST/PUT/DELETE | `/api/contacts` | Agenda de dadores de la empresa (`?city=` filtra por localidad) |
| POST | `/api/contacts/import` | Importación CSV con reporte por fila |
| POST | `/api/chat/messages` | Mensaje del camionero → respuestas del agente |
| GET | `/api/conversations`, `/api/conversations/{id}/messages` | Historial de conversaciones |
| GET | `/api/trips`, `/api/trips/{id}` | Viajes con sus matches, puntajes y métricas |
| GET | `/api/negotiations/{id}` | Transcripción de la negociación |
| GET | `/api/bookings` | Operaciones registradas |
| GET | `/api/audit-events` | Auditoría de la empresa |
| GET | `/api/loads`, `/api/sources` | Oportunidades compartidas y estado de las fuentes |

Ejemplo por consola:

```bash
TOKEN=$(curl -s -X POST localhost:8000/api/auth/login -H 'content-type: application/json' \
  -d '{"email":"admin@transportesdelcentro.demo","password":"demo1234"}' | jq -r .token)
curl -s -X POST localhost:8000/api/chat/messages -H "authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' \
  -d '{"text":"Vuelvo mañana de Córdoba a Buenos Aires vacío"}' | jq -r '.replies[].text'
```

## Próximos pasos (fuera del MVP)

- **WhatsApp real**: adaptador que traduzca el webhook del proveedor a `InboundMessage`
  (identificando al usuario por `users.phone`) y envíe los `OutboundMessage`. El orquestador no
  cambia. La API oficial de WhatsApp Business tiene costo por conversación.
- **Fuentes reales**: conectores `HttpJsonConnector`/CSV con dadores o cooperativas que
  autoricen el uso de sus datos.
- **Negociación real**: un `ShipperChannel` que envíe mensajes y devuelva `pending`; la
  respuesta retoma la negociación (ahí sí hace falta un worker o una cola).
- **Consulta a contactos**: que el agente escriba a los contactos sugeridos y sus respuestas
  entren como ofertas privadas de la empresa.
- **Avisos**: guardar búsquedas sin resultados y avisar cuando entra una carga compatible.
- **Facturación SaaS**: planes por empresa sobre la tabla `companies`.
