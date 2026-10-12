# Contactos de carga (agenda privada de cada empresa)

Cada empresa de transporte carga los dadores con los que ya trabaja: acopios, molinos,
cooperativas, distribuidoras. La agenda es **privada**: tabla `shipper_contacts` con
`company_id` y Row-Level Security, igual que los camiones. Otra empresa puede tener el mismo
teléfono en su agenda sin que ninguna vea la de la otra.

## Datos de un contacto

| Campo | Obligatorio | Notas |
|---|---|---|
| nombre | sí | |
| teléfono | teléfono o email | Se guardan sólo los dígitos. Conviene con código de país (549…) para el futuro WhatsApp |
| email | teléfono o email | |
| localidades | sí, al menos una | Dónde carga. Deben existir en el nomenclátor (`src/cargas/geo/places.py`) |
| mercaderías | no | general, refrigerada, granel, peligrosa, contenedor, hacienda |
| notas | no | |

Dentro de una empresa no se repiten teléfono ni email (índices únicos parciales).

## Alta

- **Manual**: *Configuración → Contactos de carga*, o `POST /api/contacts`.
- **Planilla CSV**: botón *Importar CSV* o `POST /api/contacts/import` con `{"csv": "<contenido>"}`.
  - Columnas: `nombre, telefono, email, localidades, mercaderias, notas`. Se aceptan sinónimos
    (`celular`, `whatsapp`, `ciudades`, `zonas`, `correo`…) y separador `,`, `;` o tabulación.
  - Varias localidades o mercaderías en una celda: separadas con `|`.
  - Si el teléfono o email ya existe en la agenda, la fila **actualiza** ese contacto.
  - Las filas inválidas se informan con su número y motivo; el resto se importa.
  - Límites: 1 MB y 2.000 filas por archivo.
  - Planilla de ejemplo: `src/cargas/web/static/contactos_ejemplo.csv` (también se descarga
    desde la pantalla).

Altas, cambios, bajas e importaciones quedan en la auditoría.

## Uso en la búsqueda

Cuando el camionero pide carga para un viaje, además de las ofertas, el agente sugiere hasta 5
contactos que cumplen todo esto:

- cargan en el origen o en una localidad cuyo desvío no supera el máximo de la empresa
  (mismo cálculo que el matching: `dist(A, C) + dist(C, B) − dist(A, B)`);
- mueven alguna mercadería que ese camión puede llevar: no se sugiere un frigorífico a un camión
  sin frío ni un contacto que sólo mueve mercadería excluida por la empresa (si no tiene
  mercaderías cargadas, se sugiere igual);
- están activos.

Se ordenan por desvío. También aparecen cuando no hay ninguna oferta compatible, que es cuando
más sirven.

**Hoy el agente sólo los sugiere: no les escribe.** El paso siguiente es que, con aprobación del
camionero, les consulte por WhatsApp ("¿tenés carga Córdoba → Buenos Aires para mañana?") y
convierta las respuestas en ofertas privadas de la empresa que entren al mismo ranking.
