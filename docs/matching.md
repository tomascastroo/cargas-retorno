# Matching y ranking

Implementado en `src/cargas/matching/engine.py` como funciones puras: sin base de datos, sin
red y sin LLM. Mismos datos ⇒ mismo resultado.

## Datos de entrada

- **Viaje**: origen A, destino B, fecha de salida, tipo de vehículo, carrocería, frío,
  pallets y kg disponibles (del camión, o menos si el mensaje lo indica).
- **Política del agente** (por empresa): tarifa mínima por km cargado, tarifa objetivo por km,
  costo variable por km, desvío máximo, días de flexibilidad, mercaderías excluidas.
- **Carga**: retiro P, entrega D, ventana de retiro, mercadería, pallets/kg, vehículos y
  carrocerías admitidos, frío, precio, si es negociable, estado, vencimiento, última
  confirmación y confiabilidad de la fuente.

## Distancias y desvío

```
base     = dist(A, B)                       el viaje que el camión hace igual (vacío)
recorrido = dist(A, P) + dist(P, D) + dist(D, B)
desvío   = max(0, recorrido − base)
cargado  = dist(P, D)
```

`dist` es haversine × 1,12 (o OSRM si está configurado). Ejemplo: Córdoba → Buenos Aires con
una carga Villa María → Buenos Aires da un desvío de ~8 km, porque Villa María queda sobre la ruta.

## Etapa 1: elegibilidad (reglas duras)

Una carga se descarta si incumple cualquiera de estas reglas. El motivo se guarda y se informa
agregado al camionero ("Descarté: 4 fuera de ruta, 1 vencidas…").

| Código | Regla |
|---|---|
| `vencida` | `valid_until` ya pasó o la ingesta la marcó `expired` |
| `no_disponible` | Estado distinto de `available` (p. ej. `taken`) |
| `fecha` | No hay un día común entre `[salida, salida + flexibilidad]` y la ventana de retiro |
| `ruta` | `desvío > desvío máximo` de la empresa |
| `vehiculo` | La carga pide tipos de vehículo y el del camión no está |
| `carroceria` | La carga pide carrocerías y la del camión no está |
| `refrigeracion` | Requiere frío y el camión no tiene |
| `capacidad` | Pallets o kg de la carga > disponibles |
| `mercaderia` | Tipo de mercadería excluido por la empresa |
| `precio` | Precio < tarifa mínima × km cargado **y** la carga no es negociable |

Dos situaciones no descartan, pero quedan marcadas:

- `debajo_tarifa_minima`: paga menos que el mínimo pero es negociable. El agente comercial
  intentará llegar al mínimo y nunca aceptará menos.
- `requiere_verificacion`: ninguna fuente la confirmó en las últimas 48 h.

## Etapa 2: puntaje (0-100)

Cada componente está en [0, 1]. El puntaje es `100 × Σ peso × componente`.

| Componente | Peso | Fórmula |
|---|---|---|
| Rentabilidad | 40 % | `ganancia neta / (tarifa objetivo × base)`, recortado a [0, 1]. `ganancia neta = precio − costo por km × desvío`: lo que se gana frente a volver vacío. El denominador es lo que pagaría una carga ideal que cubra todo el viaje a la tarifa objetivo |
| Desvío | 20 % | `1 − desvío / desvío máximo` |
| Fecha | 15 % | `1 − días de demora / (flexibilidad + 1)`: 1 si retira el día que sale |
| Ocupación | 10 % | `pallets / pallets disponibles` (o kg si no hay pallets; 0,5 sin datos) |
| Confiabilidad | 15 % | `confiabilidad de la fuente × (0,3 + 0,7 × frescura)`. La frescura vale 1 hasta 12 h desde la última confirmación y baja linealmente hasta 0 a las 72 h |

Si la carga está marcada `debajo_tarifa_minima`, el puntaje se multiplica por 0,8: sólo sirve si
la negociación prospera. Empates: mayor precio primero.

Cada match guarda en `matches.components`, `matches.metrics` (km base, desvío, cargado, ganancia
neta, mínimo, fecha de retiro, método de distancia, marcas) y una copia de la oferta
(`load_snapshot`). Así el ranking se puede auditar aunque la oferta pública cambie después.

## Ejemplo (datos demo, Córdoba → Buenos Aires, semi de 28 pallets)

| # | Carga | Desvío | Puntaje | Por qué |
|---|---|---|---|---|
| 1 | Córdoba → Buenos Aires, harina, $1.950.000 | 0 km | 97 | Mismo recorrido, buen precio, retira el día de salida |
| 2 | Villa María → Buenos Aires, leche en polvo, $1.750.000 | 8 km | 95 | Sobre la ruta y llena el camión |
| 3 | San Francisco → Buenos Aires, repuestos, $1.600.000 | 52 km | 81 | Desvío moderado, ocupa 12 de 28 pallets |

Descartadas, entre otras: Córdoba → Mendoza (ruta), carne envasada (frío), 32 pallets
(capacidad), agroquímicos (mercadería excluida), oferta vencida y oferta ya tomada.

## Cómo ajustar

Los pesos están en `WEIGHTS` y los umbrales de frescura en `FRESH_HOURS`, `STALE_HOURS` y
`VERIFY_AFTER_HOURS`. Los tests de `tests/test_matching.py` fijan el comportamiento esperado.
Conviene recalibrar los pesos con decisiones reales de camioneros (qué opción eligen) cuando
haya historial.
