# Refactor de REVIEW, progreso y ticks del Supervisor

## Objective

Separar la lógica de decisión de `REVIEW`, garantizar que cada revisión de una
sesión lea primero la memoria MCP de esa sesión, y permitir que el Supervisor
evalúe sesiones activas aunque no haya eventos nuevos. La revisión deberá
exponer medidas temporales y de progreso suficientes para distinguir trabajo
activo de estancamiento, aplicar una política configurable de ausencia de
progreso y conservar la trazabilidad de si la decisión fue determinista, del
modelo o del fallback.

## Relevant Context

- El código real está bajo `grams-app/supervisor/supervisor/`.
- `SupervisorState` está en `agent/state.py`; el grafo se construye en
  `agent/graph.py` y actualmente registra el cierre `START -> REVIEW`.
- `make_review_node` en `agent/nodes/review.py` mezcla lectura de Inbox,
  jerarquía/manifest MCP, contexto OpenCode, normalización de la respuesta del
  modelo, fallback y emisión de logs.
- `MemoryClient` expone `search`, `get_manifest` y
  `ensure_session_hierarchy`; `MCPMemoryClient.search` acepta filtros
  `project_id`, `key_id` y `category_id`, pero no un filtro
  `root_session_id`.
- `SupervisorRuntime.run_once()` sólo reclama raíces retornadas por
  `EventInbox.claimable_roots()`. El worker duerme esperando `_wakeup` o
  `runtime_poll_interval`, por lo que actualmente no revisa una sesión sin
  trabajo pendiente.
- El único endpoint público es `POST /events`; el ingress debe seguir siendo
  rápido y no debe ejecutar el grafo inline.
- La memoria se aísla por el proyecto cuyo nombre es `root_session_id` para
  sesiones no `default`; la jerarquía esperada es
  `objective/{requirements,constraints}`,
  `execution/{discoveries,attempts,errors,decisions,progress}` y
  `results/{validations,outcomes}`.
- Los tests actuales importan directamente `make_review_node`, `build_graph` y
  `SupervisorRuntime`, y usan dobles con las interfaces anteriores. El cambio
  debe actualizar esos tests en lugar de romper silenciosamente sus contratos.

## Scope

Incluye la separación de REVIEW, lectura MCP inicial, nuevos campos de estado y
contexto, ticks para sesiones activas, configuración de intervención por
estancamiento, observabilidad de la fuente de decisión y tests unitarios/de
integración.

No incluye cambios al formato de eventos recibidos, al plugin de OpenCode, al
protocolo MCP, una nueva ruta HTTP pública, multi-proceso ni una nueva política
de selección de memorias más allá de la lectura inicial por sesión.

## Expected Behavior

Una revisión con `root_session_id` inicia su preparación de memoria antes de
invocar al modelo. Para una sesión real, esa preparación resuelve el proyecto
de la sesión y realiza una lectura MCP (`search`) acotada a ese proyecto; el
resultado se coloca en `retrieved_memories` y en el contexto que verá el modelo.
Las decisiones deterministas (por ejemplo, eventos reclamados u operaciones
pendientes) siguen teniendo precedencia sobre el modelo.

Después de un resultado que deja la sesión activa, el runtime conserva esa raíz
como activa. Si no hay eventos pendientes, un `SUPERVISOR_TICK` periódico puede
invocar el mismo grafo con la instantánea checkpointed, sin fabricar ni insertar
un evento de Inbox. La política configurable puede seleccionar `INTERVENE` por
ausencia de progreso; el umbral no convierte automáticamente una herramienta
lenta en bloqueada si se observó progreso reciente.

Cada decisión deberá poder atribuirse inequívocamente a `deterministic`, `model`
o `fallback` tanto en el estado como en el evento estructurado de log.

## Functional Requirements

1. `agent/nodes/review.py` SHALL dejar de ser el único lugar que coordina toda
   la revisión. La implementación SHALL extraer funciones o un servicio con
   responsabilidades separables y testeables para: preparar contexto/memoria,
   aplicar decisiones deterministas, llamar/validar el modelo, aplicar fallback,
   y normalizar operaciones/intervenciones. `make_review_node` SHALL conservar
   una fábrica compatible con el grafo y SHALL delegar en esas unidades.
2. La separación SHALL conservar los cuatro `VALID_ACTIONS`, la precedencia de
   `claimed_events` y operaciones `link` pendientes, la normalización de IDs de
   manifest y la eliminación de `link` de los argumentos de la operación.
3. Antes de la primera llamada a `ReviewModel.decide` de cada revisión con una
   sesión no `default`, el servicio SHALL completar una lectura MCP inicial
   mediante `MemoryClient.search`, con alcance al proyecto correspondiente al
   `root_session_id`. El alcance SHALL derivarse del manifest/jeraquía existente
   (`project_id`, y claves/categorías sólo cuando ya estén determinadas), nunca
   de un ID inventado ni de otro proyecto.
4. La lectura inicial SHALL ocurrir también cuando no haya eventos recientes (en
   particular en un tick). Su resultado SHALL alimentar `retrieved_memories` y
   `build_review_context`; el orden observable de llamadas SHALL ser MCP antes
   que modelo. Para `default`, se SHALL conservar la compatibilidad existente y
   no se SHALL inventar un proyecto de sesión.
5. Un fallo de la lectura MCP SHALL ser observable, SHALL propagarse como fallo
   de la revisión y SHALL impedir la llamada al modelo para esa revisión. No se
   podrá presentar como una lista de memorias vacía ni como una lectura exitosa;
   el runtime aplicará su manejo existente de error/degradación y no marcará
   eventos como procesados por esa ejecución. El fallback queda reservado para
   errores de decisión del modelo, como en el comportamiento actual.
6. `SupervisorState` SHALL añadir, como mínimo, campos serializables para
   `activity_started_at`, `last_progress_at`, `progress_count`,
   `last_progress_kind`, `elapsed_ms`, `stalled_for_ms` y una marca de que la
   última evaluación fue un `SUPERVISOR_TICK` (el nombre puede ser
   `supervisor_tick` si se documenta). Las fechas SHALL ser ISO-8601 UTC y las
   duraciones SHALL ser milisegundos no negativos.
7. La incorporación de eventos SHALL actualizar el inicio de actividad y la
   evidencia de progreso sin borrar el historial de actividad checkpointed.
   Eventos de resultado, cambio de archivo, mensaje completado/error y otras
   evidencias explícitas de cambio SHALL poder actualizar `last_progress_at` y
   `progress_count`; una revisión/tick sin evidencia SHALL incrementar ni
   `progress_count` ni `last_progress_at`.
8. `build_review_context` SHALL incluir una sección estable y serializable de
   métricas de progreso con, como mínimo, `started_at`, `last_progress_at`,
   `elapsed_ms`/`elapsed`, `stalled_for_ms`/`stalled_for`, `progress_count` y el
   estado de actividad. Los nombres elegidos SHALL ser consistentes entre
   estado, contexto, prompt y tests; si se conservan alias legibles como
   `elapsed` y `stalled_for`, deberán representar la misma medida.
9. El prompt de REVIEW SHALL explicar que progreso reciente es evidencia para
   continuar, que ausencia de progreso sólo justifica intervenir al superar la
   política configurada y que el modelo debe citar las métricas/evidencias que
   sustentan `INTERVENE`.
10. `Config` SHALL añadir opciones validadas para habilitar la intervención por
    ausencia de progreso y para su umbral (y, si se usa, el intervalo de ticks).
    Los valores deberán poder configurarse por entorno siguiendo la convención
    existente `GRAMS_*`; el umbral deberá ser finito y no negativo y el
    intervalo, si es configurable, positivo. Los defaults SHALL preservar el
    comportamiento actual (no intervenir sólo por tiempo sin una señal
    habilitada).
11. El runtime SHALL registrar como activa una raíz cuyo resultado indique
    `session_status == "active"`, asociada al `checkpoint_id`/`thread_id` real.
    SHALL eliminarla al quedar `idle`/`DONE` o al detenerse el runtime.
12. Cuando no haya raíces reclamables, el worker SHALL evaluar las raíces
    activas elegibles mediante `SUPERVISOR_TICK` sin llamar a
    `claim_pending()` ni insertar un `SupervisorEvent`. El tick SHALL respetar
    el mismo single-flight lock y SHALL reconsultar Inbox antes de ejecutar,
    para que eventos que llegaron durante la espera tengan prioridad y no se
    pierdan.
13. Un tick SHALL restaurar el estado del hilo mediante su `thread_id`, pasar
    una entrada identificable como tick y conservar `processing_events` vacío.
    Su resultado deberá actualizar el checkpoint y podrá terminar la sesión,
    persistir memoria o intervenir igual que una revisión normal.
14. La política determinista de ausencia de progreso SHALL poder producir
    `INTERVENE` sólo cuando esté habilitada, la sesión tenga una actividad
    evaluable, `stalled_for_ms` alcance el umbral y no exista progreso reciente.
    Deberá preferir un mensaje correctivo; abortar no será el default. La
    decisión del modelo podrá sustituir esta política sólo cuando no contradiga
    las salvaguardas existentes (claimed events y operaciones pendientes).
15. `assessment` SHALL incluir `decision_source` con exactamente
    `deterministic`, `model` o `fallback`, además de la razón y métricas
    relevantes. Los logs `review_decision`, `review_fallback` y los logs de tick
    SHALL incluir `decision_source`, `root_session_id`, `run_id` cuando exista,
    y si la evaluación fue un tick. Un error del modelo SHALL dejar visible que
    se usó fallback, no etiquetarlo como `model`.
16. Los tests SHALL actualizar dobles MCP/modelo para verificar el orden de
    llamadas, tests de estado/contexto para timestamps y métricas, tests de
    descarga con progreso frente a herramienta sin progreso, tests de umbral
    habilitado/deshabilitado, tests de ticks sin eventos y tests de precedencia
    determinista/model/fallback. Deberán conservarse los tests de Inbox,
    recuperación, single-flight, intervención `default`, rutas HTTP y enlace
    MCP existentes.

## Non-Functional Requirements

- Los timestamps usados para medir intervalos deberán usar un reloj monotónico
  internamente; sólo los valores persistidos/expuestos como fechas serán UTC.
- El estado y contexto deberán ser JSON-serializables y acotados; no se deberán
  guardar respuestas completas de OpenCode ni payloads sensibles en métricas o
  logs.
- Un tick sin trabajo no deberá crear actividad ocupada: como máximo una
  revisión por raíz elegible y por ciclo/intervalo, respetando el lock actual.
- La refactorización no deberá aumentar la concurrencia de invocaciones del
  grafo por encima de una.

## Affected Components

- `grams-app/supervisor/supervisor/agent/nodes/review.py` y posibles módulos
  nuevos bajo `agent/`.
- `agent/state.py`, `agent/prompts.py`, `agent/graph.py` y
  `agent/nodes/read_inbox.py`.
- `runtime/supervisor.py`.
- `config.py` y la composición en `app.py`.
- Tests existentes en `grams-app/tests/test_supervisor_runtime.py`,
  `test_event_server.py`, `test_review_model.py` y nuevos tests bajo ese mismo
  directorio.

## Constraints

- No cambiar `POST /events`, el transporte MCP Streamable HTTP ni los nombres de
  las tools MCP.
- No llamar a MCP, OpenCode, modelo ni LangGraph desde el handler HTTP.
- Mantener `AsyncSqliteSaver`, el `thread_id` formado a partir de
  `checkpoint_id` y `root_session_id`, los leases y la semántica de ack/fallo.
- `root_session_id` es el límite de aislamiento; nunca se mezclará memoria de
  proyectos.

## Edge Cases

- Sesión `default`, `root_session_id` ausente o proyecto MCP inexistente.
- Primera revisión sin eventos, tick con estado checkpointed incompleto y tick
  concurrente con llegada de un evento.
- `last_progress_at` ausente, timestamp inválido, reloj adelantado y duración
  cero; no deberán producir duraciones negativas ni una intervención espuria.
- Progreso de una compilación/descarga lenta, ausencia total de bytes/salida,
  retry equivalente sin cambio y progreso que ocurre justo en el umbral.
- Modelo timeout/JSON inválido, lectura MCP fallida y decisión inválida; cada
  camino debe conservar una fuente observable.
- Reinicio del runtime con raíces activas checkpointed pero sin registro en
  memoria del proceso.

## Error Handling

Los errores MCP/modelo/OpenCode deberán conservar su tipo y emitirse con el
evento estructurado correspondiente. Un fallo de modelo podrá usar el fallback
determinista existente y deberá marcar `decision_source=fallback`. Un fallo MCP
no podrá ocultarse como memoria vacía; deberá bloquear la decisión dependiente
de esa lectura o activar el fallback documentado. Los ticks fallidos deberán
seguir la misma política de reintento/estado degradado del runtime y no deberán
marcar eventos como procesados, porque no contienen eventos reclamados.

## Acceptance Criteria

- `make_review_node` sigue siendo utilizable por `build_graph`, pero sus
  responsabilidades pueden probarse sin levantar todo el grafo.
- En un test con un `MemoryClient` espía, la llamada `search` por el proyecto de
  la sesión precede a `model.decide` y sus resultados aparecen en contexto.
- Un contexto de REVIEW serializado contiene las métricas requeridas y una
  herramienta con progreso no se clasifica como estancada.
- Una herramienta sin progreso durante el umbral configurable puede producir un
  `INTERVENE` determinista; con la opción deshabilitada no lo produce por tiempo
  solamente.
- Una sesión activa recibe un `SUPERVISOR_TICK` aunque Inbox no tenga eventos;
  una sesión idle no recibe ticks y una llegada concurrente se reclama por la
  ruta normal.
- Logs/`assessment` distinguen correctamente `deterministic`, `model` y
  `fallback`, incluyendo timeout del modelo.
- La suite relevante pasa y `git diff --check` no reporta errores.

## Test Scenarios

1. Sesión real con manifest, search MCP y modelo: comprobar aislamiento y orden.
2. Primera revisión sin eventos y revisión de `default`.
3. Eventos reclamados: `READ_INBOX` determinista sin llamadas externas previas.
4. Modelo válido, modelo timeout y JSON inválido: comprobar las tres fuentes.
5. Descarga con bytes/salida periódicos frente a descarga sin progreso y retry.
6. Umbral habilitado, deshabilitado, cero, valor inválido y progreso justo antes
   del umbral.
7. Runtime con sesión activa y Inbox vacío: tick; luego sesión idle: ningún
   tick.
8. Evento persistido mientras se prepara un tick: no se incorpora artificialmente
   al snapshot del tick y se procesa en el siguiente ciclo.
9. Reinicio con checkpoint de sesión activa y sin eventos pendientes.
10. Regresión de `/events`, single-flight, leases, ack, intervención real y
    `default`.

## Out of Scope

No se especifica un algoritmo para inferir bytes de cada herramienta a partir de
payloads arbitrarios, ni un nuevo endpoint de métricas, almacenamiento histórico
de todas las muestras, ajuste automático del umbral o intervención humana.

## Assumptions

- “Lectura inicial MCP” significa una operación `memory_search` a través de
  `MemoryClient.search`, no una llamada HTTP ad hoc; `get_manifest` y
  `ensure_session_hierarchy` son preparación y no sustituyen esa lectura.
- La actividad/progreso se derivará de los eventos normalizados disponibles; si
  un evento no aporta evidencia de cambio, se tratará como ausencia de progreso.
- Las raíces activas se registrarán al completar una ejecución durante la vida
  del runtime. No se exige una consulta nueva al checkpoint store para enumerar
  sesiones anteriores al arranque, porque la interfaz actual de `SupervisorRuntime`
  no expone esa enumeración; un restart sólo deberá conservar ticks para raíces
  que vuelvan a observarse por un evento.
- El umbral de ausencia de progreso se expresa en segundos en configuración y
  en milisegundos en estado/contexto, salvo que se documente explícitamente otra
  unidad única.
