# Logging coloreado y medición de atraso del Supervisor GRAMS

## Objective

Agregar observabilidad de consola al Supervisor Python para seguir un evento
desde OpenCode hasta su resultado, identificar en qué etapa se emplea el
tiempo y medir el atraso del Supervisor respecto del instante en que OpenCode
emitió el evento, sin registrar payloads ni credenciales.

## Relevant Context

- El entrypoint es `supervisor.app:app`; el runtime single-flight está en
  `supervisor/runtime/supervisor.py`.
- `POST /events` persiste primero en el Inbox SQLite y luego llama a
  `runtime.notify()`. Actualmente ya existen logs mediante `logging` y
  `GRAMS_LOG_LEVEL`.
- Un `run_id` identifica la invocación del grafo; `root_session_id` agrupa el
  trabajo y `event_id` identifica la fila durable. El estado también contiene
  `supervisor_checkpoint_id`; el `thread_id` efectivo se deriva de él y de la
  sesión.
- Los nodos existentes son `REVIEW`, `READ_INBOX`, `MEMORY_OPERATION` e
  `INTERVENE`. Las dependencias externas incluyen OpenCode HTTP, MCP Go y el
  modelo compatible con OpenAI.
- El envelope normalizado de OpenCode conserva un timestamp y el payload
  completo. El timestamp puede no estar presente en entradas genéricas del
  endpoint.

## Scope

Incluye un formatter/handler de logging Python para consola, eventos
correlacionados, mediciones de latencia, clasificación configurable de atraso,
configuración documentada y pruebas unitarias/integración del Supervisor.

No incluye cambios al plugin, al envelope HTTP, a OpenCode, al protocolo MCP,
un sistema de métricas externo ni almacenamiento persistente de trazas.

## Expected Behavior

1. Cada log operacional usa un `event_name` estable y campos `key=value` o su
   equivalente estructurado. Los logs de una recepción/procesamiento incluyen,
   cuando estén disponibles, `event_id`, `session_id`, `root_session_id`,
   `run_id`, `checkpoint_id`/`thread_id`, `retry_count` y `duration_ms`.
2. El log muestra tiempos con reloj UTC, pero calcula duraciones con reloj
   monotónico. Las duraciones se expresan en milisegundos y no bloquean el
   event loop.
3. Para un timestamp válido de OpenCode se registra `source_lag_ms` al reclamar
   el evento y al completarlo. También se registra siempre `queue_lag_ms`
   (`claim` menos `received_at`), `run_duration_ms`, latencia de cada nodo y de
   cada llamada externa. Si no hay timestamp comparable, el campo es `null` y
   no se inventa una estimación.
4. Un atraso que supere el umbral configurado genera un evento `lag_detected`
   de nivel `WARNING`, identificando si corresponde a `source_lag_ms` o
   `queue_lag_ms`. El valor medido sigue apareciendo aunque no haya umbral.
5. Colorear sólo afecta la salida de consola; los `LogRecord` y sus campos
   siguen siendo utilizables por tests y consumidores no interactivos.

## Functional Requirements

### Eventos y niveles

La implementación SHALL emitir, como mínimo, estos eventos (con los campos de
correlación aplicables):

| `event_name` | Nivel | Datos mínimos |
| --- | --- | --- |
| `supervisor_startup`, `supervisor_shutdown` | `INFO` | estado y duración de shutdown cuando aplique |
| `event_received`, `event_persisted` | `INFO` | `event_id`, tipo, sesión/raíz, `persist_duration_ms` |
| `event_persistence_failed` | `ERROR` | tipo de error sanitizado, sesión/raíz si se conocen |
| `runtime_wakeup`, `runtime_status_changed` | `DEBUG` / `INFO` | estado anterior/nuevo y motivo; wakeups coalescidos no se duplican artificialmente |
| `run_claimed`, `run_started`, `run_completed` | `INFO` | `run_id`, raíz, `thread_id`, cantidad, outcome y latencias |
| `node_started`, `node_completed` | `DEBUG` | nodo, `run_id`, `node_duration_ms`, acción siguiente si existe |
| `external_call_started`, `external_call_completed` | `DEBUG` | servicio (`opencode`, `mcp`, `model`), operación, duración y outcome |
| `external_call_failed`, `runtime_error` | `ERROR` | servicio/categoría, tipo y mensaje sanitizado |
| `event_acknowledged` | `INFO` | `event_id`, `run_id`, estado final y duración total si disponible |
| `event_retry_scheduled`, `event_failed`, `lease_conflict` | `WARNING` / `ERROR` | `event_id`, intento, próxima acción y error sanitizado |
| `lag_detected` | `WARNING` | clase de lag, medición, umbral, sesión/raíz y `event_id` |

Los niveles SHALL significar: `DEBUG` para detalle de alta frecuencia,
`INFO` para hitos normales, `WARNING` para fallback/reintento/atraso y `ERROR`
para fallos que impiden completar una etapa. `CRITICAL` conserva su significado
de fallo irrecuperable si se usa.

El formatter SHALL mapear niveles a colores ANSI: `DEBUG` gris tenue, `INFO`
cian, `WARNING` amarillo, `ERROR` rojo y `CRITICAL` rojo intenso/magenta. No
SHALL insertar códigos ANSI cuando el modo sea `never` o la salida automática
no sea un TTY.

### Correlación y latencia

1. `event_id` SHALL conservar el ID durable; `session_id` SHALL conservar la
   sesión fuente y `root_session_id` SHALL usar `default` cuando ese sea el
   fallback del Inbox.
2. `run_id` SHALL identificar la ejecución actual del grafo y no se SHALL
   confundir con un `run_id` recibido de OpenCode. Si ambos existen, el log
   SHALL diferenciarlos explícitamente.
3. El contexto de correlación SHALL propagarse a logs de nodos, clientes
   OpenCode/MCP/modelo y transiciones de Inbox sin cambiar sus contratos HTTP o
   MCP.
4. `source_lag_ms` SHALL calcularse sólo con el timestamp de emisión de
   OpenCode y un timestamp UTC comparable. Un timestamp inválido, ausente o en
   el futuro SHALL producir `source_lag_ms=null` y un diagnóstico de skew/parseo
   sin clasificarlo como atraso real.
5. `queue_lag_ms` SHALL medir desde `received_at` hasta `processing_at`/claim;
   `run_duration_ms` desde el inicio hasta el fin de `graph.ainvoke`; y cada
   latencia de nodo/servicio SHALL cubrir sólo esa operación.

### Configuración

- SHALL conservarse `GRAMS_LOG_LEVEL`, con niveles Python válidos y `INFO` por
  defecto.
- SHALL añadirse `GRAMS_LOG_COLOR=auto|always|never`, con `auto` por defecto.
- SHALL añadirse `GRAMS_LOG_LAG_WARN_MS`, opcional y no negativo. Si no está
  definido, se registran mediciones pero no se emite una alerta por umbral.
- La configuración programática `Config` SHALL exponer los mismos valores y
  validarlos antes del startup. Ninguna configuración SHALL contener o imprimir
  `MODEL_API_KEY`.
- La configuración del logging SHALL ser idempotente al crear varias apps en
  tests y no SHALL añadir handlers duplicados.

## Non-Functional Requirements

- El logging SHALL ser asíncrono desde el punto de vista del flujo de negocio:
  ningún formateo o medición SHALL esperar red, SQLite, LangGraph o OpenCode.
- Los mensajes SHALL ser legibles en consola y suficientemente estables para
  capturarse con `caplog`; el color no SHALL contaminar los campos estructurados.
- No se SHALL registrar payload completo, contenido de mensajes, contexto de
  OpenCode, argumentos de tools, headers de autorización, API keys ni secretos.
  Los errores externos se limitarán a tipo y mensaje sanitizado/truncado.

## Affected Components

- `grams-app/supervisor/app.py` y `config.py`.
- `supervisor/api/events.py`, `inbox/`, `runtime/`, `agent/nodes/`,
  `memory/client.py` y `opencode/client.py`.
- Tests bajo `grams-app/tests/`; no se requiere modificar el smoke test del
  receptor salvo para verificar que sigue funcionando.

## Constraints

- Mantener `POST /events`, `GRAMS_EVENT_ENDPOINT`, la persistencia durable y el
  runtime single-flight.
- No introducir otro endpoint, broker, trace store, proceso worker ni formato
  de evento externo.
- La correlación debe ser best-effort para entradas que carezcan de sesión,
  run o timestamp; no se deben fabricar identificadores de OpenCode.

## Edge Cases

- JSON escalar, `null`, evento sin sesión o timestamp y timestamp malformado.
- Timestamp de OpenCode futuro por desfase de relojes: registrar skew, no lag
  negativo.
- Reintentos, expiración de lease, conflicto de lease, cancelación y fallo de
  una llamada externa: conservar correlación y outcome.
- Muchos `notify()` concurrentes: registrar wakeup coalescido sin sugerir que
  se crearon múltiples workers.
- Salida redirigida, TTY, `GRAMS_LOG_COLOR=never` y niveles que filtren logs
  `DEBUG`.

## Error Handling

Un fallo al emitir o formatear un log no SHALL marcar un evento como procesado,
alterar su retry ni detener el runtime. Los errores de logging se manejarán de
forma que no oculten el error de negocio. Un valor inválido de nivel, color o
umbral SHALL rechazar la configuración antes de aceptar solicitudes.

## Acceptance Criteria

1. Una ejecución de prueba produce una cadena correlacionable
   `event_received → event_persisted → run_claimed → run_started →
   node_completed/external_call_completed → run_completed → event_acknowledged`
   con los IDs esperados y sin payload/secretos.
2. La cadena contiene `queue_lag_ms`, `run_duration_ms` y latencias por nodo y
   servicio; con timestamp válido contiene `source_lag_ms` y, al superar el
   umbral, `lag_detected` en `WARNING`.
3. Cada nivel usa el color especificado en consola; `auto` no colorea una salida
   no TTY y `never` nunca emite ANSI.
4. `INFO` oculta el detalle por nodo/llamada (`DEBUG`), mientras `DEBUG` lo
   muestra; los campos de correlación permanecen disponibles en ambos casos.
5. La configuración por entorno y por `Config` valida nivel, color y umbral,
   y crear/detener apps repetidamente no duplica líneas de log.
6. Fallos, retries, cancelación y lease conflict se registran con nivel y
   outcome correctos sin cambiar las transiciones del Inbox existentes.

## Test Scenarios

1. Capturar logs con `caplog` durante un `POST /events` y un `run_once()` con
   fake graph; verificar eventos, IDs, orden lógico, latencias no negativas y
   ausencia de payload/API key.
2. Usar un timestamp de OpenCode controlado y relojes inyectables/falsos para
   verificar `source_lag_ms`, `queue_lag_ms`, umbral, timestamp ausente,
   malformado y futuro.
3. Hacer lentos los fakes de OpenCode, MCP y modelo; verificar que cada llamada
   tiene inicio/fin, servicio, operación y duración, y que el HTTP ingress no
   espera esas llamadas.
4. Ejecutar cada nodo y un recorrido completo del grafo; verificar
   `node_started/node_completed`, `run_id`, `thread_id` y resultado final.
5. Provocar retry, `FAILED`, conflicto de lease, excepción, cancelación y
   timeout de shutdown; verificar eventos correlacionados y niveles sin ack
   falso.
6. Probar `GRAMS_LOG_LEVEL`, los tres modos de color, TTY/no-TTY y creación
   repetida de la app; verificar filtrado, ANSI y ausencia de handlers duplicados.
7. Mantener los tests existentes de Inbox/runtime y ejecutar el smoke test de
   `supervisor.app:app` para confirmar que la observabilidad no cambia el
   contrato del receptor.

## Out of Scope

- Métricas Prometheus/OpenTelemetry, dashboards, alertas remotas o persistencia
  de spans.
- Medir el tiempo interno de OpenCode más allá del timestamp que éste envíe y
  de la latencia de las llamadas HTTP realizadas por el Supervisor.
- Cambiar la política de scheduling, los timeouts externos o la semántica de
  retries/leases.

## Ambigüedad relevante

El código actual conserva el payload de OpenCode, pero no extrae un campo de
timestamp ni define su nombre en el modelo del Inbox. Antes de implementar se
debe confirmar que el campo es `payload["timestamp"]` y que representa emisión
del evento; si el receptor usa otro nombre, se debe documentar/adaptar esa
lectura sin modificar el envelope ni fingir precisión.

## Assumptions

- El timestamp normalizado de OpenCode representa el instante de emisión del
  evento y es comparable con el reloj UTC del Supervisor; sin él sólo puede
  medirse el atraso desde la recepción HTTP.
- `run_id` del runtime y el `thread_id` existente son las unidades de
  correlación disponibles; no se añade una base de datos de trazas.
- El umbral de atraso es una ayuda de diagnóstico, no una decisión automática
  de pausar o acelerar el Supervisor.
