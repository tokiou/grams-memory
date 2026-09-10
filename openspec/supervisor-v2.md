# Especificación: Supervisor local v2 — integración real

Esta especificación sustituye la versión anterior de
`openspec/supervisor-v2.md` como fuente de verdad para la siguiente fase del
Supervisor. Conserva los contratos de ingress, Inbox, runtime, checkpoints,
tests y comandos de ejecución, pero reemplaza las integraciones externas
simuladas por clientes HTTP reales.

## Objective

Construir un Memory Supervisor local que reciba eventos normalizados mediante
FastAPI, los confirme de forma durable en un Inbox SQLite y los procese con un
único ciclo concurrente de runtime basado en LangGraph y `AsyncSqliteSaver`.
El Supervisor debe comunicarse con el MCP Go mediante el transporte MCP
Streamable HTTP, comunicarse con OpenCode mediante su API HTTP real y usar un
modelo compatible con OpenAI configurado por `OPENROUTER_DEPLOYMENT`,
`OPENROUTER_API_KEY` y `OPENROUTER_BASE_URL`. La fase no debe contener implementaciones
simuladas en producción y debe conservar la recuperación, observabilidad,
tests y comandos de ejecución del receptor existente.

## Relevant Context

- El punto de entrada ASGI es `supervisor.app:app`, servido con
  `uvicorn supervisor.app:app --app-dir grams-app/supervisor`.
- La aplicación usa FastAPI con `redirect_slashes=False` y expone `POST
  /events`. El plugin de OpenCode envía JSON a `GRAMS_EVENT_ENDPOINT`, cuyo
  valor local por defecto termina en
  `http://host.docker.internal:8765/events`; ese contrato no debe cambiar.
- El receptor debe aceptar cualquier valor JSON válido: objeto, array, string,
  número, booleano o `null`.
- `.env.example` ya define `OPENROUTER_DEPLOYMENT`, `OPENROUTER_API_KEY` y
  `OPENROUTER_BASE_URL`, y Docker Compose usa esas variables para configurar un
  proveedor OpenAI-compatible de OpenCode.
- La implementación debe conservar las responsabilidades observadas en la
  fase anterior: Inbox SQLite con leases y estados, runtime single-flight con
  `asyncio.Event` y lock, grafo LangGraph, lifecycle y observabilidad.
- El contrato de memoria de esta fase se limita a las tools reales del MCP Go.
  No fija un modelo definitivo de memorias, nodos, aristas o política de
  selección.
- OpenCode puede ejecutarse en otro proceso o contenedor. Por tanto, la ruta
  de red desde el proceso del Supervisor hasta la API HTTP de OpenCode es un
  requisito de despliegue, no una conexión implícita al plugin que envía
  eventos.

## Scope

Incluye:

1. El árbol de paquetes obligatorio descrito abajo.
2. Un ingress FastAPI con persistencia durable antes de responder.
3. Un Inbox SQLite asíncrono con estados, leases, reintentos y recuperación.
4. Un runtime single-flight con wakeup coalescente, polling y recheck.
5. Un grafo LangGraph con `AsyncSqliteSaver`.
6. Estado operacional del runtime y estado serializable del agente.
7. Un cliente MCP Go real mediante Streamable HTTP.
8. Un cliente OpenCode HTTP real para contexto, `prompt_async` y `abort`.
9. Un cliente de modelo OpenAI-compatible configurado por las tres variables de
   modelo indicadas.
10. Lifecycle, errores, logs, tests y criterios de ejecución de la fase
    anterior, adaptados a las integraciones reales.

No incluye cambios al plugin, cambios al formato de los eventos, un nuevo
endpoint HTTP público del Supervisor, despliegue multi-proceso, autenticación
adicional no exigida por los servicios externos ni una política de memoria más
allá de la selección de las tools disponibles.

## Estructura obligatoria

El paquete de implementación SHALL tener esta estructura lógica:

```text
grams-app/supervisor/
  supervisor/
    app.py                           # composición de la aplicación
    config.py                        # configuración y validación
    api/                             # ingress FastAPI
    inbox/                           # dominio y persistencia del Inbox
    runtime/                         # worker, single-flight y lifecycle
    agent/                           # estado y grafo LangGraph
    memory/                          # cliente MCP Go Streamable HTTP
    opencode/                        # cliente OpenCode HTTP
    platform/
      sqlite/                        # conexión, migración y checkpoint SQLite
```

`supervisor/` SHALL contener `app.py`, `config.py` y exactamente los
directorios `api/`, `inbox/`, `runtime/`, `agent/`, `memory/`, `opencode/` y
`platform/`; `platform/` SHALL contener `sqlite/`. No SHALL existir una
implementación paralela en el nivel superior con módulos planos que dupliquen
esas responsabilidades.

Las responsabilidades SHALL separarse así:

- `app.py` SHALL componer configuración, recursos, clientes, grafo, runtime y
  lifecycle; no SHALL contener SQL ni nodos del grafo.
- `config.py` SHALL cargar y validar configuración sin abrir conexiones ni
  iniciar tasks.
- `api/` SHALL contener las rutas y la traducción HTTP; no SHALL reclamar
  eventos ni ejecutar LangGraph inline.
- `inbox/` SHALL contener los modelos del envelope/evento y la API durable del
  Inbox.
- `runtime/` SHALL ser dueño del worker, single-flight, wakeup, estado
  operacional y parada cooperativa.
- `agent/` SHALL contener `SupervisorState` y la construcción del grafo.
- `memory/` SHALL contener el protocolo y el cliente real MCP Streamable HTTP.
- `opencode/` SHALL contener el protocolo y el cliente real OpenCode HTTP.
- `platform/sqlite/` SHALL encapsular la conexión SQLite async, migraciones,
  pragmas y creación/cierre de `AsyncSqliteSaver`.

No SHALL existir `event_server.py` una vez migrado el receptor a FastAPI. El
único entrypoint ASGI público es `supervisor.app:app`.

## Contratos externos

### MCP Go mediante Streamable HTTP

El cliente de `memory/` SHALL usar el transporte MCP Streamable HTTP real
contra la URL configurable del MCP Go. SHALL establecer y cerrar la sesión MCP
según el ciclo de vida de la instancia del Supervisor; no SHALL sustituir el
protocolo por llamadas HTTP ad hoc, stdio, una base SQLite local de memoria ni
una implementación local de MCP.

Las capacidades abstractas del Supervisor SHALL mapearse exactamente a estas
tools MCP, sin renombrarlas ni ocultar el error de una llamada:

| Operación abstracta | Tool MCP Go real |
| --- | --- |
| `search` | `memory_search` |
| `get` | `memory_get` |
| `create` | `memory_create` |
| `update` | `memory_update` |
| `neighbors` | `memory_neighbors` |
| `expand` | `memory_expand` |
| `link` | `memory_link` |
| `archive` | `memory_archive` |
| `restore` | `memory_restore` |

El cliente SHALL enviar los argumentos estructurados que requiere cada tool,
conservar el resultado estructurado y asociar cada llamada a
`event_id`/`run_id` cuando estén disponibles. La validación de argumentos y la
forma exacta de cada resultado SHALL respetar el contrato publicado por el
MCP Go; el Supervisor no SHALL inventar una respuesta exitosa cuando la tool
devuelva un error.

### OpenCode HTTP

El cliente de `opencode/` SHALL ser un cliente HTTP real de la API de
OpenCode, con base URL configurable y timeout configurable. SHALL exponer como
mínimo operaciones async equivalentes a:

- obtener el contexto de una sesión;
- enviar una intervención mediante `prompt_async`;
- abortar una ejecución o tarea mediante `abort`.

Las llamadas SHALL usar los endpoints y payloads de la versión de OpenCode
configurada, y SHALL propagar códigos HTTP, respuestas inválidas y timeouts
como errores de integración. El cliente no SHALL simular respuestas ni mutar
estado local para aparentar una intervención.

La configuración de despliegue SHALL documentar que la API HTTP de OpenCode
debe ser alcanzable desde el proceso o contenedor del Supervisor usando la
base URL configurada. Un plugin que pueda alcanzar `/events` no implica que
el Supervisor pueda alcanzar OpenCode; ambas rutas de red deben verificarse
por separado.

### Modelo OpenAI-compatible

El componente de revisión SHALL usar un modelo compatible con la API de
OpenAI, configurado exclusivamente por:

- `OPENROUTER_DEPLOYMENT`: identificador del deployment/modelo;
- `OPENROUTER_API_KEY`: credencial para la API;
- `OPENROUTER_BASE_URL`: base URL de OpenRouter compatible con OpenAI.

Las tres variables SHALL validarse antes de startup. El valor de
`OPENROUTER_API_KEY` no SHALL registrarse y el cliente SHALL tratar errores HTTP,
respuestas inválidas y timeouts como errores del ciclo de procesamiento. No se
permite un modelo local simulado como valor por defecto de producción.

## Expected Behavior

1. Durante startup se carga y valida la configuración, se verifica la
   configuración de MCP Go, OpenCode y modelo, se crea el directorio de la
   base si es necesario, se inicializa el Inbox, se aplican migraciones
   idempotentes, se prepara `AsyncSqliteSaver`, se abren los clientes externos,
   se construye el grafo y se inicia exactamente un worker antes de aceptar
   solicitudes.
2. `POST /events` lee el cuerpo completo, decodifica JSON, inserta el envelope
   como `PENDING`, espera el commit durable, llama a `notify()` y responde `202
   Accepted`. No espera al grafo ni a una llamada MCP, OpenCode o modelo.
3. Un wakeup es sólo una señal; SQLite es la fuente de verdad. Un evento
   recibido mientras el worker está ocupado queda pendiente y se reclama en el
   siguiente recheck.
4. El runtime reclama un lote, lo agrupa por `root_session_id` usando el
   fallback documentado cuando no exista, ejecuta el grafo una vez por grupo,
   confirma sólo los eventos procesados correctamente y libera/reintenta o
   marca como terminales los demás.
5. Un evento que llegue antes, durante o inmediatamente después de `END` no se
   pierde ni se incluye artificialmente en el lote ya reclamado. Se descubre en
   el recheck sin otra solicitud HTTP.
6. El grafo lee contexto real de OpenCode cuando la revisión lo requiere,
   solicita al modelo una decisión serializable y ejecuta, mediante MCP, la
   tool seleccionada de la tabla anterior. Una intervención usa
   `prompt_async` o `abort` según el resultado de revisión y la operación
   solicitada.
7. Al reiniciar, los eventos pendientes y los `PROCESSING` cuyo lease expiró
   vuelven a ser elegibles según la política de intentos. Ningún evento se
   elimina silenciosamente ni se marca `PROCESSED` por una ejecución
   incompleta.
8. Los clientes externos se cierran durante shutdown aunque el worker falle o
   exceda el timeout configurado.

## Functional Requirements

### Configuración y composición

1. `supervisor.app` SHALL exponer `create_app()` y una aplicación ASGI `app`.
2. `config.py` SHALL soportar como mínimo `GRAMS_DB_PATH`,
   `GRAMS_RUNTIME_POLL_INTERVAL`, `GRAMS_INBOX_BATCH_SIZE`,
   `GRAMS_PROCESSING_LEASE_SECONDS`, `GRAMS_MAX_ATTEMPTS`,
   `GRAMS_SHUTDOWN_TIMEOUT` y `GRAMS_CHECKPOINT_ID`.
3. SHALL existir configuración para la URL del MCP Go y la base URL de
   OpenCode, con defaults o nombres documentados de forma explícita. La
   implementación de esta fase usará `GRAMS_MCP_URL` y
   `OPENCODE_BASE_URL`, salvo que el entorno de ejecución ya provea nombres
   equivalentes documentados.
4. `OPENROUTER_DEPLOYMENT`, `OPENROUTER_API_KEY` y `OPENROUTER_BASE_URL` SHALL ser valores no
   vacíos y SHALL validarse antes de startup.
5. La configuración SHALL rechazar rutas vacías, identificadores vacíos,
   URLs inválidas, números no positivos y valores que no se puedan convertir al
   tipo configurado.
6. Cada instancia SHALL crear sus propias conexiones, saver, clientes y tasks;
   no se permiten recursos globales compartidos entre instancias.
7. Shutdown SHALL dejar de aceptar trabajo nuevo, despertar al worker,
   esperar el timeout configurado y cerrar saver, SQLite, MCP, OpenCode y
   cliente de modelo.

### FastAPI ingress

8. `api/` SHALL registrar exactamente `POST /events` como ruta pública y
   conservar `redirect_slashes=False`; `/events/` no es un alias.
9. `POST /events` SHALL aceptar cualquier JSON válido sin imponer un esquema de
   objeto. El envelope SHALL conservar el valor completo y derivar `event_type`
   de `type` cuando sea string; para los demás casos usará un fallback
   documentado.
10. La respuesta `202` SHALL ocurrir después del commit durable y antes de
    cualquier llamada al grafo, MCP, OpenCode o modelo.
11. Un cuerpo vacío, mal codificado o JSON inválido SHALL responder `400`, no
    insertar filas y no notificar al runtime.
12. Un error de apertura, inserción o commit del Inbox SHALL responder `503` y
    no SHALL confirmar la recepción como `202`.
13. Rutas no declaradas SHALL responder `404` y métodos no soportados para
    `/events` SHALL responder `405`.
14. `notify()` SHALL ser una señal no bloqueante y no SHALL ejecutar SQLite,
    clientes externos ni LangGraph dentro del handler.

### Inbox durable

15. `inbox/` SHALL exponer una API async equivalente a `initialize()`,
    `insert(envelope)`, `claim_batch(limit, lease)`, `ack(event_id, lease_id)`,
    `fail(event_id, lease_id, error, retry_at)`, `recover_expired(now)`,
    `has_ready()` y `close()`.
16. La persistencia SHALL usar SQLite mediante `platform/sqlite/` y no SHALL
    ejecutar consultas bloqueantes en el event loop.
17. La tabla SHALL conservar como mínimo `id`, `received_at`, `type`,
    `session_id` nullable, `payload`, `status`, `retry_count`, `available_at`,
    `lease_until` nullable, `error` nullable y `processed_at` nullable. Puede
    conservar `root_session_id`, `source_event`, `processing_at` y `lease_id`.
18. `payload` SHALL permitir round-trip de todos los tipos JSON aceptados.
19. Los estados SHALL ser exactamente `PENDING`, `PROCESSING`, `PROCESSED` y
    `FAILED`, con transiciones `PENDING -> PROCESSING -> PROCESSED`,
    `PROCESSING -> PENDING` y `PROCESSING -> FAILED`.
20. `insert()` SHALL ser transaccional y durable antes de retornar; dos envíos
    separados no SHALL deduplicarse por igualdad de payload.
21. `claim_batch()` SHALL seleccionar sólo filas disponibles o leases
    expirados, reclamarlas atómicamente, asignar un lease y aumentar
    `retry_count`. Dos reclamaciones concurrentes no SHALL devolver el mismo
    evento con un lease activo.
22. `ack()` SHALL comprobar el lease vigente. Confirmar un evento ya
    `PROCESSED` será idempotente; un lease obsoleto no SHALL confirmar
    erróneamente el evento.
23. `fail()` SHALL conservar payload, error, retry_count y `available_at`,
    limpiar el lease y elegir `PENDING` o `FAILED` según
    `GRAMS_MAX_ATTEMPTS`. El backoff SHALL ser determinista en tests.
24. `recover_expired()` SHALL aplicar la misma política a leases expirados y
    tratar explícitamente un `PROCESSING` sin lease sin borrarlo.
25. La inicialización SHALL ser idempotente y configurar journal WAL,
    `busy_timeout` y foreign keys cuando correspondan.

### Runtime single-flight y lifecycle

26. `runtime/` SHALL exponer operaciones async equivalentes a `start()`,
    `run_once()` y `stop()`, además de una señal `notify()` no bloqueante.
27. SHALL existir exactamente un worker por runtime. Una barrera single-flight
    impedirá dos `run_once()` activos simultáneamente, incluso desde tests.
28. `notify()` SHALL ser coalescente mientras exista una señal pendiente e
    ignorar nuevas señales después de iniciar shutdown.
29. El worker SHALL esperar wakeup o polling, comprobar trabajo, reclamar lote,
    ejecutar el grafo, aplicar `ack`/`fail` y reconsultar SQLite antes de dormir.
30. La comprobación final y limpieza del wakeup SHALL coordinarse para no
    perder una notificación concurrente. El polling SHALL permanecer como
    salvaguarda.
31. `END` no SHALL limpiar por sí mismo el wakeup ni declarar vacío el Inbox.
    Un evento insertado durante el grafo o entre `END` y el sleep se procesará
    sin otra llamada HTTP.
32. Un fallo de grafo, MCP, OpenCode o modelo SHALL afectar sólo los claims
    activos, conservar `last_error`, reintentar hasta el límite y dejar
    `FAILED` al último intento. No SHALL detener permanentemente el worker.
33. Una cancelación durante un nodo o una llamada externa no SHALL producir
    `PROCESSED`; el evento debe volver a `PENDING` o quedar recuperable por
    expiración de lease.
34. El runtime SHALL publicar estados `STARTING`, `IDLE`, `RUNNING`,
    `DEGRADED`, `STOPPING` y `STOPPED`, con snapshot no bloqueante que incluya
    transición, `active_run_id`, tamaño de lote, último error, wakeup y
    contadores de claimed/done/retry/failed.

### Grafo, modelo y clientes

35. `agent/` SHALL construir un `StateGraph` con los nodos conceptuales
    `REVIEW`, `READ_INBOX`, `MEMORY_OPERATION`, `INTERVENE` y `END`. No SHALL
    existir un nodo adicional dedicado a leer memoria.
36. `READ_INBOX` SHALL consumir únicamente los eventos que el runtime ya
    reclamó y pasó en el estado; no SHALL reclamar por segunda vez.
37. `REVIEW` SHALL obtener el contexto necesario mediante OpenCode HTTP,
    invocar el modelo OpenAI-compatible y producir una decisión serializable.
    La decisión puede terminar, intervenir o seleccionar una de las nueve
    operaciones abstractas de la tabla MCP.
38. `MEMORY_OPERATION` SHALL despachar exclusivamente a la tool MCP Go que
    corresponda, retornar su resultado al estado y volver a `REVIEW` cuando
    proceda. No SHALL ejecutar una operación local equivalente.
39. `INTERVENE` SHALL usar el cliente OpenCode HTTP para `prompt_async` o
    `abort`, según la decisión revisada, y SHALL conservar la respuesta o error
    sanitizado en el estado.
40. Las rutas normales del grafo SHALL poder terminar en `END`; sólo después de
    completar con éxito el procesamiento del grupo el runtime podrá hacer
    `ack`.
41. `SupervisorState` SHALL ser serializable e incluir como mínimo `run_id`,
    `checkpoint_id` o `thread_id`, `root_session_id`, eventos reclamados,
    eventos actuales, `last_seen_event_id`, decisión/resultado de review y
    estado de sesión/actividad.
42. `platform/sqlite/` SHALL preparar un `AsyncSqliteSaver`, mantenerlo abierto
    durante el runtime y cerrarlo durante shutdown.
43. Cada ejecución SHALL recibir un `thread_id`/checkpoint estable derivado de
    `GRAMS_CHECKPOINT_ID` y de la unidad de sesión. Un checkpoint corrupto o
    incompatible no SHALL marcar eventos como `PROCESSED`.
44. Los nodos no SHALL abrir conexiones directamente: toda llamada externa
    atravesará el cliente async correspondiente.
45. Las implementaciones de producción SHALL ser los clientes reales descritos
    en esta especificación. Los dobles de prueba, si se necesitan, sólo podrán
    vivir en código de tests o en servidores de prueba inyectados y no podrán
    ser el valor por defecto del Supervisor.

### Observabilidad

46. SHALL existir logging estructurado o equivalente para startup, shutdown,
    recepción, insert, wakeup, claim, comienzo/fin de grafo y nodo, llamadas y
    errores MCP/OpenCode/modelo, `ack`, retry, recuperación, `FAILED`, cambios
    de estado y conflictos single-flight.
47. Cada procesamiento SHALL poder correlacionarse mediante `event_id`,
    `root_session_id`, `run_id`, `checkpoint_id`/`thread_id`, retry_count y
    duración. No SHALL imprimirse el payload completo ni `OPENROUTER_API_KEY`.
48. Los errores SHALL conservar tipo y mensaje sanitizado en logs y
    `last_error`, diferenciando SQLite, LangGraph, MCP Go, OpenCode, modelo y
    shutdown.

## Non-Functional Requirements

- Inbox, checkpoint y clientes externos SHALL ser async o ejecutarse fuera del
  event loop mediante una abstracción explícita.
- La respuesta HTTP SHALL confirmar durabilidad, no procesamiento; la latencia
  de MCP, OpenCode y modelo no puede formar parte del camino de respuesta.
- La solución SHALL ser determinista para el Inbox/runtime y funcionar en un
  único proceso y una única instancia local.
- Las transacciones y leases SHALL ser seguros ante concurrencia local y no
  perder eventos entre claim y ack.
- Las credenciales, payloads y contexto potencialmente sensibles SHALL tratarse
  como datos no logueables por defecto.
- Las conexiones HTTP SHALL tener timeout, cierre explícito y límites de
  respuesta apropiados para evitar tasks colgadas indefinidamente.

## Affected Components

- `grams-app/supervisor/supervisor/app.py` y todos sus subpaquetes.
- Configuración de entorno para MCP Go, OpenCode y las tres variables del
  modelo.
- `requirements.txt`, sólo si la instalación limpia no contiene clientes HTTP,
  FastAPI, Uvicorn, `aiosqlite`, LangGraph y
  `langgraph-checkpoint-sqlite` compatibles.
- Tests bajo `grams-app/tests/` para ingress, Inbox, runtime, grafo, lifecycle,
  clientes MCP/OpenCode/modelo, recuperación, observabilidad y carreras.
- El smoke test existente del plugin, que SHALL conservar su endpoint,
  envelope y comando de ejecución.

## Constraints

- No cambiar el formato, normalización ni URL por defecto del plugin de
  OpenCode.
- No introducir otro servidor HTTP, otra cola durable, otro worker Supervisor,
  Redis, broker externo, soporte multi-proceso ni un transporte MCP distinto
  de Streamable HTTP.
- No hacer SQL desde handlers FastAPI, lógica de grafo desde `api/` ni llamadas
  HTTP directas fuera de `memory/`, `opencode/` o el cliente de modelo.
- El MCP Go y OpenCode son dependencias reales de ejecución. El Supervisor no
  SHALL aparentar disponibilidad cuando una de sus URLs no sea alcanzable.
- `AsyncSqliteSaver` es obligatorio; no se acepta saver síncrono ni checkpoint
  sólo en memoria.
- La estructura de directorios indicada es normativa.
- El endpoint de eventos sigue siendo local al receptor; la alcanzabilidad de
  OpenCode HTTP desde el Supervisor debe resolverse en la red del despliegue.

## Edge Cases

- JSON `null`, arrays, escalares, cuerpo vacío, bytes inválidos y JSON truncado
  deben seguir las respuestas especificadas sin corromper el Inbox.
- Dos eventos con payload idéntico enviados por separado son dos recepciones.
- Un evento puede volver a reclamarse tras expirar su lease, pero no mientras
  el lease vigente pertenezca a otra ejecución.
- Puede llegar un wakeup antes de que el worker espere, durante un lote,
  después de `END` o repetido muchas veces; ninguna secuencia puede crear un
  segundo worker o perder un evento durable.
- Un Inbox vacío, lote menor que el límite y sesión sin `session_id` son
  válidos.
- Una respuesta MCP válida con error, una tool ausente, JSON HTTP inválido,
  timeout o cierre prematuro de stream son fallos de integración y no éxitos.
- OpenCode puede estar temporalmente inalcanzable, responder 4xx/5xx o cerrar
  la conexión durante `context`, `prompt_async` o `abort`; el evento no debe
  marcarse `PROCESSED` en esos casos.
- El modelo puede devolver una decisión malformada, tardar más que el timeout o
  devolver un error de autenticación; todos son fallos observables y
  reintentables según la política del Inbox.
- La caída del proceso después de `PROCESSING` y antes de `ack` debe ser
  recuperable por lease.
- `stop()` durante una llamada HTTP lenta debe respetar el timeout y dejar el
  evento no confirmado.
- Corrupción o incompatibilidad de checkpoint no debe convertir un evento en
  `PROCESSED`.

## Error Handling

- Parseo HTTP: `400`; fallo de Inbox/SQLite durante recepción: `503`; ningún
  error de recepción devuelve `202` sin commit durable.
- Fallo de MCP, OpenCode, modelo o grafo: log correlacionado, `last_error`,
  retry hasta el límite y luego `FAILED`; el batch no se confirma antes del
  éxito.
- Error de configuración o imposibilidad de abrir MCP, OpenCode o modelo en
  startup: abortar startup antes de aceptar solicitudes y dejar el error
  visible.
- Conflicto de lease: transición atómica sin ack falso; el evento válido se
  recupera o reclama de nuevo.
- Timeout de shutdown: registrar, cancelar/esperar tasks según política
  explícita y cerrar los recursos que sigan disponibles.
- Los mensajes de error externos SHALL sanear credenciales, headers de
  autorización, payloads completos y contexto sensible.

## Acceptance Criteria

1. Una instalación limpia puede importar `supervisor.app:app` y el árbol del
   paquete coincide con la estructura obligatoria, sin módulos planos antiguos
   que dupliquen responsabilidades.
2. El Supervisor abre una sesión MCP Go mediante Streamable HTTP y una prueba
   de integración verifica llamadas a las nueve tools exactas: `memory_search`,
   `memory_get`, `memory_create`, `memory_update`, `memory_neighbors`,
   `memory_expand`, `memory_link`, `memory_archive` y `memory_restore`.
3. Las llamadas de contexto, `prompt_async` y `abort` llegan a un servidor
   OpenCode HTTP de prueba a través del cliente real, con errores y timeouts
   propagados.
4. El modelo se construye usando `OPENROUTER_DEPLOYMENT`, `OPENROUTER_API_KEY` y
   `OPENROUTER_BASE_URL`; startup rechaza cualquiera ausente y ningún log expone la
   API key.
5. Un JSON válido en `POST /events` produce `202` sólo después de existir una
   fila `PENDING` durable, sin esperar clientes externos ni grafo.
6. JSON inválido/cuerpo vacío produce `400` sin inserción; fallo de SQLite
   produce `503`; rutas y métodos conservan `404/405`.
7. La tabla conserva payload completo, estados, retry_count, leases y errores;
   dos `claim_batch` concurrentes no devuelven eventos duplicados.
8. Cien notificaciones y llamadas concurrentes a `run_once()` demuestran un
   máximo de un procesamiento activo y todos los eventos sanos terminan
   `PROCESSED`.
9. Un evento insertado durante un grafo o en la ventana `END`/wakeup se
   descubre en el recheck y termina procesado sin una nueva solicitud HTTP.
10. Un `PROCESSING` con lease expirado se recupera tras reiniciar; los intentos
    aumentan y el evento termina `PROCESSED` tras recuperación o `FAILED` al
    alcanzar el límite.
11. Un fallo simulado únicamente en la infraestructura de test de MCP,
    OpenCode, modelo o LangGraph conserva payload y error, reintenta de forma
    observable y no marca `PROCESSED` antes del éxito.
12. El grafo visita `REVIEW`, `READ_INBOX` y las operaciones seleccionadas,
    usa `MEMORY_OPERATION` para las tools reales, no contiene un nodo dedicado
    a leer memoria y persiste/recupera checkpoint con `AsyncSqliteSaver`.
13. El snapshot operacional distingue `STARTING`, `IDLE`, `RUNNING`,
    `DEGRADED`, `STOPPING` y `STOPPED`, y los logs correlacionan un evento
    desde ingress hasta `PROCESSED`, retry o `FAILED` sin payload completo.
14. Startup y shutdown repetidos de varias instancias no dejan workers/tasks ni
    conexiones SQLite, MCP, OpenCode o modelo abiertos.
15. El receptor y el smoke test existente del plugin continúan funcionando con
    `supervisor.app:app`, sin modificar `GRAMS_EVENT_ENDPOINT`, el envelope ni
    el formato de normalización.
16. La documentación y configuración de despliegue muestran explícitamente
    cómo hacer alcanzable OpenCode HTTP desde el Supervisor y una prueba falla
    de forma visible cuando esa ruta no es alcanzable.

## Test Scenarios

1. Importar `supervisor.app:app` y verificar árbol de paquetes, startup y
   shutdown.
2. Enviar objeto, array, string, número y `null`; verificar `202`, cuerpo
   vacío, round-trip del payload y procesamiento.
3. Enviar cuerpo vacío, bytes inválidos y JSON truncado; verificar `400`, cero
   inserciones y cero wakeups de trabajo.
4. Inyectar un Inbox temporal y comprobar que la fila existe antes de liberar
   el handler; usar una dependencia HTTP lenta de test para demostrar que HTTP
   no espera al grafo.
5. Ejecutar dos reclamaciones concurrentes y verificar exclusividad de lease,
   ack idempotente, conflicto de lease y transiciones válidas.
6. Notificar antes de dormir, durante un batch, después de `END` y muchas
   veces; verificar un solo worker, recheck y ausencia de pérdida o duplicado.
7. Insertar un `PROCESSING` abandonado, avanzar reloj/lease, reiniciar y
   verificar recuperación, backoff/retry_count y límite terminal.
8. Levantar un servidor MCP Streamable HTTP de test, ejecutar cada una de las
   nueve operaciones y verificar el nombre de tool, argumentos, resultado y
   cierre de sesión.
9. Levantar un servidor OpenCode HTTP de test, verificar `context`,
   `prompt_async` y `abort`, y fallar por separado cada operación para comprobar
   retry, `last_error` y ausencia de ack falso.
10. Levantar un endpoint OpenAI-compatible de test, verificar deployment,
    base URL y autorización; probar respuesta malformada, timeout y error HTTP.
11. Instrumentar el grafo para verificar estado serializable, persistencia del
    checkpoint async entre dos ciclos/reinicios y que no existe un nodo
    dedicado a leer memoria.
12. Consultar el snapshot operacional durante startup, procesamiento, error y
    shutdown; comprobar estados, contadores, correlación y ausencia de secretos
    o payloads completos en logs.
13. Cancelar el runtime durante una operación async y verificar que el evento
    no queda falsamente `PROCESSED` y puede recuperarse por retry/lease.
14. Ejecutar el receptor y el smoke test existente del plugin con el entrypoint
    `supervisor.app:app`, sin cambiar endpoint ni formato.

## Execution Commands

Los criterios de ejecución conservan estos comandos/entrypoints existentes:

```bash
uvicorn supervisor.app:app --app-dir grams-app/supervisor
python grams-app/tests/receptor-opencode/run_test.py
```

La implementación SHALL mantener ambos entrypoints funcionales. Los tests
automatizados del Supervisor SHALL poder ejecutarse con el runner ya adoptado
por `grams-app/tests/`, sin exigir Docker para las pruebas unitarias de Inbox,
runtime y grafo; las pruebas de integración externa pueden levantar servidores
de test controlados.

## Out of Scope

- Cambiar el plugin, su normalización, `GRAMS_EVENT_ENDPOINT` o el smoke test.
- Definir un nuevo esquema definitivo de memorias, clasificación, scoring,
  selección o política de retención.
- Implementar otro servidor MCP, transporte alternativo o una base local que
  reemplace al MCP Go.
- Crear una API pública adicional para estado operacional.
- TLS, rate limiting, escalado horizontal, Redis, colas externas y soporte
  multi-worker/multi-proceso.
- Cambiar el servidor OpenCode o implementar un modelo propietario; el
  Supervisor sólo consume sus APIs configuradas.

## Assumptions and ambiguities

- Se asume una sola instancia de proceso y runtime. Los leases protegen
  reclamaciones concurrentes locales, no alta disponibilidad.
- Se asume que el MCP Go publica las nueve tools con los nombres exactos de la
  tabla y que su contrato de argumentos/resultados está disponible para la
  implementación.
- Se asume que la API HTTP de OpenCode expone operaciones equivalentes a
  contexto, `prompt_async` y `abort`; las rutas concretas dependen de la versión
  de OpenCode configurada y deben documentarse junto con su base URL.
- Se fija `GRAMS_MCP_URL` y `OPENCODE_BASE_URL` como nombres de configuración
  de esta fase cuando el entorno no tenga nombres equivalentes ya establecidos.
- `FAILED` es terminal después de `GRAMS_MAX_ATTEMPTS`; cambiar a reintentos
  indefinidos requeriría una especificación separada.
- Los dobles de prueba se limitan al código/servidores de tests. La instalación
  de producción requiere MCP Go, OpenCode HTTP y un endpoint de modelo
  alcanzables desde el Supervisor.
