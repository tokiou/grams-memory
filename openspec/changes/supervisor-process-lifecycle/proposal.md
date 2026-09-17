# Servicio de lifecycle de procesos del Supervisor

## Objective

Implementar el servicio determinista que coordina la identidad y las
transiciones de procesos del Supervisor, manteniendo la memoria durable en
Memory MCP. El servicio debe poder consultar el proceso activo sin efectos
secundarios, cerrar el proceso anterior cuando existe una transición real y
crear el proceso sucesor mediante el contrato MCP existente. La topología de
LangGraph debe usar este servicio a través de nodos con responsabilidades
separadas y verificables.

## Relevant Context

- `SupervisorState` ya contiene `project_id`, `active_process_id`,
  `process_continuity`, `pending_process_transition` y
  `pending_process_summary`.
- `MemoryClient` ya expone `get_active_process`, `create_process`,
  `close_process` y `list_processes` en
  `grams-app/supervisor/memory/client.py`.
- El MCP Go expone `process_get_active`, `process_create` y `process_close`.
  `process_create` recibe `project_id`, `name`, `description` y
  `predecessor_id`, crea un proceso `ACTIVE` y sus categorías `STRATEGY`,
  `EVIDENCE` y `SUMMARY` de forma atómica.
- El modelo MCP permite los estados `ACTIVE`, `SUCCEEDED`, `FAILED`,
  `SUPERSEDED` y `ABANDONED`. Sólo los cuatro últimos son estados de cierre.
- El servicio actual `make_ensure_active_process` intenta resolver el proyecto
  mediante `ensure_session_project` cuando falta `project_id`; esa operación
  puede crear datos. Esto no satisface el requisito de que `ensure_active` sea
  sólo lectura y debe quedar explícitamente separado del nuevo contrato.
- La topología actual es
  `ASSESS_PROCESS_CONTINUITY -> WRITE_PROCESS_SUMMARY -> CLOSE_CURRENT_PROCESS
  -> START_NEW_PROCESS` para un pivot, y reutiliza `load_process_context`
  después de crear el sucesor.
- `docs/v1/GRAPH.md` y `ARCHITECTURE.md` establecen que el LLM clasifica y
  resume, mientras que los nodos deterministas leen, persisten y actualizan el
  lifecycle.

## Scope

Incluye:

- Un servicio/protocolo de lifecycle con operaciones de consulta del activo,
  cierre y creación de sucesor.
- La implementación de los nodos `ensure_active_process`,
  `close_current_process` y `start_new_process` usando ese servicio.
- Contratos de entradas, salidas, invariantes, errores y orden de llamadas MCP.
- Ajustes mínimos de wiring en `graph.py` y pruebas unitarias/topológicas.

No incluye la heurística de pivot, prompts, revisión, actualización general de
memoria, Inbox, OpenCode, nuevos endpoints MCP ni cambios al servidor Go salvo
los estrictamente necesarios para hacer cumplir el contrato ya expuesto.

## Expected Behavior

1. `ensure_active` recibe un `project_id` ya resuelto y realiza únicamente una
   lectura de `get_active_process(project_id)`. Si existe, devuelve su ID y
   nunca llama a `create_process`, `close_process`, `ensure_session_project` ni
   a otra operación mutante.
2. La ausencia de proceso activo es un estado de error explícito; no provoca
   la creación implícita del primer proceso en `ensure_active`.
3. Cuando `ASSESS_PROCESS_CONTINUITY` produce `NEW_PROCESS`, el flujo genera el
   resumen pendiente, cierra el proceso actual como `SUPERSEDED` y sólo después
   crea por MCP un sucesor `ACTIVE` con `predecessor_id` igual al proceso
   cerrado.
4. El sucesor pertenece al mismo `project_id`, tiene nombre no vacío derivado
   de la transición pendiente y queda como `active_process_id`. El contexto del
   sucesor se carga antes de continuar con la extracción de memoria.
5. Una decisión `SAME_PROCESS` no cierra ni crea procesos. Una decisión de
   revisión `CLOSE_PROCESS` sin transición pendiente cierra el proceso con el
   resultado terminal indicado y finaliza el ciclo.
6. Los fallos de cierre impiden crear el sucesor. Los fallos de creación no
   reabren silenciosamente el predecesor ni inventan otro proceso.

## Functional Requirements

### Servicio y contrato MCP

1. El servicio SHALL depender de `MemoryClient` mediante inyección explícita;
   no SHALL acceder directamente a SQLite ni construir llamadas HTTP propias.
2. SHALL exponer contratos equivalentes a:

   ```python
   async def ensure_active(project_id: str) -> ProcessReference
   async def close_current(process_id: str, status: TerminalProcessStatus) -> ProcessReference
   async def create_successor(
       project_id: str,
       predecessor_id: str,
       name: str,
       description: str = "",
   ) -> ProcessReference
   ```

   Los nombres concretos pueden adaptarse a las convenciones del paquete, pero
   los parámetros, efectos y resultados SHALL conservar esta semántica.
3. `ensure_active` SHALL llamar exactamente a `get_active_process` para el
   proyecto y SHALL validar que una respuesta no nula contiene un ID.
4. `close_current` SHALL aceptar sólo `SUCCEEDED`, `FAILED`, `SUPERSEDED` o
   `ABANDONED`, llamar a `close_process(process_id, status)` y validar el ID y
   estado terminal devueltos.
5. `create_successor` SHALL llamar a `create_process` con
   `project_id`, `name`, `description` y `predecessor_id`; SHALL exigir que la
   respuesta sea un proceso `ACTIVE` del mismo proyecto y con un ID distinto
   del predecesor.
6. El vínculo predecessor/successor SHALL conservarse usando
   `predecessor_id`, que es el vínculo soportado por `process_create`. No se
   SHALL llamar a `link` con IDs de proceso, pues `link` opera sobre memorias.
7. El servicio SHALL tratar respuestas MCP con forma inválida, IDs ausentes,
   proyecto incorrecto, estado incorrecto o ID repetido como errores, sin
   publicar un resultado parcialmente válido.

### Nodos del grafo

1. `ensure_active_process` SHALL validar `root_session_id` y `project_id`.
   Resolver el proyecto o crear la jerarquía de sesión SHALL ser una operación
   previa del runtime, no una responsabilidad de este nodo. El nodo SHALL
   delegar la consulta al servicio y devolver al menos `project_id` y
   `active_process_id`.
2. `assess_process_continuity` SHALL ser el único responsable de clasificar
   `SAME_PROCESS`/`NEW_PROCESS` y de producir el nombre/razón de una transición.
   No SHALL crear, cerrar ni mutar memoria.
3. `write_process_summary` SHALL producir `pending_process_summary` y el
   `process_outcome`; no SHALL cerrar el proceso ni escribir directamente en
   MCP.
4. `close_current_process` SHALL validar que hay `active_process_id`, resumen
   pendiente cuando el contrato del cierre lo requiera y un outcome terminal.
   SHALL persistir el Summary mediante el mecanismo de escritura de memoria
   existente antes de invocar `close_current`; no SHALL usar un LLM.
5. En un pivot, `close_current_process` SHALL usar `SUPERSEDED` salvo que una
   decisión terminal explícita y compatible indique otro outcome. SHALL dejar
   disponible la transición pendiente para el router.
6. `start_new_process` SHALL ejecutarse sólo después de un cierre exitoso y
   sólo si existe `pending_process_transition`. SHALL delegar la creación al
   servicio, actualizar `active_process_id` con el nuevo ID y no SHALL inventar
   nombre, pivot o relaciones.
7. El grafo SHALL conservar las aristas
   `write_process_summary -> close_current_process -> start_new_process ->
   load_new_process_context`. No SHALL existir un camino que cree un sucesor
   antes de cerrar el predecesor.
8. Ninguno de estos nodos SHALL usar un LLM. La decisión de continuidad, el
   resumen y la decisión de revisión permanecen en sus nodos LLM separados.

### Estado y resultados

1. Los IDs y snapshots en `SupervisorState` SHALL ser coordinación transitoria,
   no una segunda fuente de memoria semántica.
2. El resultado de cada operación SHALL permitir distinguir `created`,
   `closed`, `not_found` y `failed` sin inferirlo desde texto libre.
3. El nodo SHALL conservar errores operativos en el contrato existente de
   `cycle_errors` o propagar una excepción según la política vigente del
   runtime; nunca SHALL confirmar un ciclo cuyo cierre/creación requerido
   falló.

## Non-Functional Requirements

- Las operaciones SHALL ser async y no bloquear el event loop.
- El servicio SHALL ser determinista dado un `MemoryClient` falso y no SHALL
  mantener estado durable local.
- SHALL ser seguro ante reintentos: una respuesta de conflicto MCP no SHALL
  producir un segundo sucesor ni ocultar que el estado requiere reconciliación.
- Los logs, si se añaden, SHALL registrar operación e IDs sin payloads ni
  credenciales.

## Affected Components

- `grams-app/supervisor/agent/` (nuevo servicio, contratos y nodos de lifecycle).
- `grams-app/supervisor/agent/graph.py` y, sólo si es necesario, `state.py` o
  `schemas.py`.
- `grams-app/supervisor/memory/client.py` únicamente para alinear el protocolo
  con el servicio, sin cambiar el protocolo MCP existente.
- Tests bajo `grams-app/tests/`.

## Constraints

- SQLite Inbox sigue siendo la fuente durable de eventos; no se guardarán
  eventos en estado LangGraph como memoria durable.
- Cada proyecto SHALL tener como máximo un proceso `ACTIVE`, conforme a la
  restricción del MCP Go.
- No añadir categorías: cada proceso sucesor debe recibir exactamente
  `STRATEGY`, `EVIDENCE` y `SUMMARY` a través de `process_create`.
- No crear un agente separado ni permitir que el LLM ejecute herramientas.
- Debe respetarse el contrato y estados actuales del MCP Go, incluyendo que
  `process_close` sólo cierra procesos activos.

## Edge Cases

- Falta `project_id`: error de validación antes de cualquier llamada MCP en
  `ensure_active`.
- No hay proceso activo: error explícito y cero llamadas mutantes.
- Respuesta activa sin ID, sucesor no `ACTIVE`, proyecto distinto o sucesor con
  el mismo ID: error de contrato.
- Transición sin nombre o con `predecessor_id` ausente: rechazo antes de crear.
- Proceso ya cerrado, predecesor inexistente o conflicto por otro proceso
  activo: propagar como error de lifecycle y no crear otro sucesor.
- Se pierde la respuesta después de `process_close` o `process_create`: no
  repetir automáticamente una mutación sin una política de reconciliación que
  confirme el estado mediante lecturas MCP.
- `pending_process_transition` vacío en `start_new_process`: no-op/error de
  precondición, pero nunca creación espontánea.

## Error Handling

Los errores de validación se detectarán antes de las mutaciones. Los errores
MCP se propagarán como errores de servicio con operación e identificadores
sanitizados. Una transición fallida SHALL dejar el proceso anterior en el
estado confirmado por MCP y SHALL impedir el ACK exitoso del lote cuando el
runtime considere la transición parte obligatoria del ciclo. La recuperación
de un timeout posterior a una mutación SHALL consultar el estado real; no se
debe asumir que la mutación falló ni duplicarla ciegamente.

## Acceptance Criteria

1. Con un proceso activo existente, `ensure_active` devuelve su ID y el fake
   registra únicamente `get_active_process`; no se llama a create/close ni a
   `ensure_session_project`.
2. Sin proceso activo o sin `project_id`, `ensure_active` falla con un error
   verificable y no realiza mutaciones.
3. Un pivot ejecuta, en orden observable, escritura del Summary, `close_process`
   con `SUPERSEDED`, `create_process` con el mismo proyecto y
   `predecessor_id`, y finalmente carga el contexto del sucesor.
4. El sucesor devuelto es `ACTIVE`, tiene ID distinto y queda en
   `active_process_id`; el predecesor queda terminal.
5. `SAME_PROCESS` y cierre sin transición no invocan `create_process`; el
   segundo sólo invoca el cierre terminal correspondiente.
6. Respuestas MCP inválidas, conflictos y fallos de red producen errores sin
   sucesor falso ni ACK falso.
7. La topología compilada conserva el orden y las rutas descritas, y las
   pruebas existentes de Inbox/runtime continúan pasando.

## Test Scenarios

1. Unitario de `ensure_active` con proyecto e ID en mayúsculas/minúsculas según
   el formato que ya normaliza `_field`; comprobar resultado y llamadas.
2. Unitarios de validación para proyecto ausente, proceso ausente, ID ausente y
   estados no terminales.
3. Unitario de `create_successor` que compruebe todos los argumentos MCP,
   incluido `predecessor_id`, y rechace una respuesta que no sea `ACTIVE`.
4. Unitario de cierre que compruebe `SUPERSEDED`, los otros tres estados
   terminales y el rechazo de `ACTIVE`.
5. Prueba de integración de nodos con fake MCP que verifique el orden
   `summary -> close -> create -> load` y que no haya creación antes del cierre.
6. Pruebas de rutas del grafo para `SAME_PROCESS`, `NEW_PROCESS`,
   `CLOSE_PROCESS` sin sucesor y `CONTINUE`.
7. Prueba de fallo en close y fallo/timeout en create: comprobar que no se
   intenta una mutación posterior ni se confirma el lote.
8. Prueba contra el MCP Go o su fake de contrato que compruebe un solo activo,
   predecessor del mismo proyecto, categorías exactas y estados terminales.

## Out of Scope

- Definir el algoritmo que decide cuándo hay un pivot.
- Elegir el nombre definitivo de procesos más allá de validar y propagar el
  nombre de la transición.
- Diseñar retries, leases o reconciliación distribuida nuevos.
- Cambiar el esquema SQLite, el protocolo JSON-RPC/MCP o el servidor OpenCode.
- Implementar prompts, review, expansión de grafo o deduplicación semántica.

## Ambiguity / Assumptions

- **Ambigüedad relevante:** el código actual permite que
  `ensure_active_process` llame a `ensure_session_project` y cree jerarquía.
  Para cumplir “ensure_active sólo lectura” esta especificación asume que
  `project_id` estará resuelto antes del nodo. Si el runtime no puede
  garantizarlo, debe acordarse una operación de resolución separada; no se
  debe ocultar una escritura dentro de `ensure_active`.
- Se asume que `pending_process_transition` contiene al menos
  `name` (y opcionalmente `description` y `reason`) y que el outcome de un
  pivot es `SUPERSEDED`.
- Se asume que la persistencia del Summary puede reutilizar la ruta de escritura
  de memoria existente; `MemoryClient` no expone una operación específica de
  Summary ni relaciones entre IDs de proceso.
- Se asume que el orden close-then-create requerido por la topología es
  suficiente para la consistencia entre llamadas MCP separadas; la atomicidad
  entre ambas llamadas queda fuera de este servicio.
