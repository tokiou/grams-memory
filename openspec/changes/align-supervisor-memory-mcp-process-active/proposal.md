# Alinear `process_get_active` entre Supervisor y Memory MCP

## Objective

Corregir el desajuste de contrato por el que el Supervisor invoca la
herramienta MCP `process_get_active` pero el servidor Memory MCP en ejecución
responde `unknown tool`. La implementación deberá hacer que el contrato
publicado, el cliente Python del Supervisor y el servidor Go sean verificables
de extremo a extremo, sin ocultar el fallo como un proceso inexistente ni
confirmar eventos del Inbox cuyo ciclo no pudo completarse.

## Relevant Context

- `grams-app/supervisor/memory/client.py` implementa el adaptador JSON-RPC
  Streamable HTTP. `MCPMemoryClient.get_active_process(project_id)` llama a
  `tools/call` con nombre `process_get_active` y argumentos `{ "id":
  project_id }`.
- `grams-app/memory-mcp/internal/mcp/tools/memory.go` registra actualmente
  `process_get_active`, cuyo handler delega en `GetActiveProcess` y convierte
  `ErrProcessNotFound` en resultado nulo. El mismo archivo registra
  `process_create`, `process_get`, `process_list` y `process_close`.
- `MCPMemoryClient._call_tool` ya transforma respuestas MCP con `isError` en
  `RuntimeError`, y `_request` transforma errores JSON-RPC en `RuntimeError`.
  La corrección debe conservar estas fronteras, no añadir acceso directo a la
  base de datos desde el Supervisor.
- El runtime reclama eventos mediante `EventInbox.claim_pending`; ante una
  excepción del grafo llama a `fail_batch`. `mark_failed_batch` deja el lote en
  `PENDING` hasta alcanzar `max_attempts`, y después lo deja en `FAILED`.
  `ack_batch` sólo debe ejecutarse tras completar correctamente el ciclo.
- Los tests existentes relevantes son `test_memory_client.py`,
  `test_process_service.py`, `test_read_inbox.py`,
  `test_supervisor_v2_resilience.py` y
  `test_latest_fixes_regressions.py`. Las pruebas Go del MCP se ejecutan con
  `go test ./...` desde `grams-app/memory-mcp`.
- **Observación del incidente:** aunque el código fuente inspeccionado contiene
  el registro de `process_get_active`, el endpoint MCP observado no publica la
  herramienta. Esto indica una deriva entre el binario/servidor desplegado y el
  código o registro esperado; la implementación debe verificar el servidor
  realmente utilizado, no sólo el código fuente.

## Scope

Incluye:

- Un contrato único y documentado para `process_get_active`.
- La alineación del registro Go, el cliente Python y cualquier wiring de
  arranque o empaquetado necesario para que el endpoint publicado incluya la
  herramienta.
- Pruebas de listado de herramientas y de llamada real Supervisor -> MCP,
  incluyendo proceso existente, ausencia de proceso y error `unknown tool`.
- Pruebas de regresión que demuestren que un fallo MCP no hace ACK del Inbox y
  que conserva la semántica existente de reintento, leases, cohortes y estado
  terminal.

No incluye cambios al modelo de procesos, al esquema SQLite, a la política de
revisión, a la topología de LangGraph ni la sustitución de `process_get_active`
por una búsqueda implementada en Python.

## Expected Behavior

1. `tools/list` del servidor Memory MCP incluye exactamente el nombre canónico
   `process_get_active` con un esquema de entrada compatible con `{ "id":
   "<project-id>" }`.
2. Una llamada del Supervisor a `get_active_process(project_id)` llega al MCP
   como `tools/call` de `process_get_active` y devuelve un objeto proceso cuando
   existe uno activo.
3. Si no existe proceso activo, el MCP devuelve resultado nulo (no un error de
   herramienta) y el cliente conserva esa distinción.
4. Si el servidor no publica la herramienta, responde `unknown tool`, es
   inalcanzable o devuelve una respuesta inválida, el cliente produce un error
   operativo explícito. Nunca debe convertir ese caso en `None` ni crear,
   cerrar o sustituir un proceso como fallback implícito.
5. Si ese error ocurre durante un ciclo del Supervisor, los eventos reclamados
   se marcan mediante la ruta existente de fallo (`PENDING` o `FAILED` según
   `max_attempts`), sus leases se liberan y no se marcan `PROCESSED`.

## Functional Requirements

### Contrato Memory MCP

1. El servidor SHALL publicar `process_get_active` en `tools/list` en todas las
   configuraciones de arranque soportadas.
2. La herramienta SHALL aceptar un objeto con el campo obligatorio `id`, cuyo
   valor representa un `project_id`; no se SHALL requerir `project_id` como
   nombre alternativo ni aceptar que el Supervisor dependa de una convención
   no publicada.
3. La herramienta SHALL devolver un proceso con, como mínimo, `id`,
   `project_id` y `status` cuando exista un proceso `ACTIVE` para el proyecto.
   El resultado SHALL pertenecer al proyecto solicitado y tener estado
   `ACTIVE`.
4. La ausencia de proceso activo SHALL devolverse como resultado nulo según
   el comportamiento actual del handler; no SHALL representarse como
   `unknown tool` ni como proceso sintético.
5. Un `project_id` vacío o una entrada malformada SHALL producir un error de
   validación MCP; no SHALL consultar ni mutar otro proyecto.
6. El registro de la herramienta SHALL estar incluido en el servidor que usan
   las pruebas de integración y en el artefacto/entrypoint usado en ejecución.
   No es suficiente que exista una función no alcanzable por el servidor.
7. No SHALL existir una segunda herramienta con nombre parecido que obligue al
   cliente a probar aliases. El nombre canónico para esta operación es
   `process_get_active`.

### Cliente y Supervisor

8. `MCPMemoryClient.get_active_process` SHALL invocar únicamente
   `process_get_active` con `{ "id": project_id }` y SHALL devolver un
   diccionario de proceso o `None`.
9. El cliente SHALL conservar en el error la operación `process_get_active` y
   un diagnóstico acotado del MCP, incluyendo `unknown tool` cuando ese sea el
   motivo. No SHALL convertir errores de transporte, JSON-RPC o herramienta en
   ausencia de proceso.
10. El servicio de lifecycle y los nodos existentes SHALL seguir usando la
    interfaz `get_active_process`; no SHALL acceder directamente a SQLite ni
    duplicar la implementación de `GetActiveProcess`.
11. Si se añade una comprobación de capacidades mediante `tools/list`, ésta
    SHALL fallar de forma explícita cuando falte `process_get_active` y SHALL
    evitar llamadas mutantes como consecuencia de esa comprobación. La
    comprobación no SHALL sustituir la prueba de llamada real.

### Inbox y runtime

12. Un `unknown tool` durante `SupervisorRuntime.run_cycle` SHALL propagarse
    como fallo del ciclo y SHALL pasar por `_fail_claimed`/`fail_batch`.
13. Mientras el lote esté en `PROCESSING`, un fallo MCP SHALL impedir cualquier
    `ack` o `ack_batch` exitoso para ese lote.
14. El fallo SHALL conservar el error original suficientemente identificado en
    el campo de error del Inbox, sin registrar credenciales ni payloads
    completos.
15. Un reintento posterior SHALL poder reclamar el lote cuando el estado sea
    `PENDING` y `available_at` sea alcanzable. Al superar `max_attempts`, el
    estado SHALL ser `FAILED`, conforme al repositorio existente.
16. La corrección SHALL preservar la exclusión de cohortes activas por
    `root_session_id`, la renovación de leases y la protección de
    `ack_batch` contra leases obsoletos.

## Non-Functional Requirements

- Las llamadas SHALL permanecer async y no bloquear el event loop.
- La solución SHALL ser compatible con el handshake MCP y el header de sesión
  ya implementados por `MCPMemoryClient`.
- Los errores y logs SHALL identificar herramienta, proyecto de forma segura y
  operación, sin credenciales ni contenido completo de eventos.
- El contrato probado SHALL ser determinista y reproducible contra un servidor
  MCP real o un fake HTTP que implemente la misma superficie JSON-RPC.

## Affected Components

- `grams-app/memory-mcp/internal/mcp/tools/memory.go` y el wiring/entrypoint
  del servidor Go, si el binario usado no registra la herramienta.
- `grams-app/supervisor/memory/client.py`, sólo para validación del contrato y
  diagnóstico de errores si fuese necesario.
- `grams-app/supervisor/agent/runtime.py` y la integración del ciclo, sólo si
  hace falta demostrar que el error llega a `fail_batch` sin ACK.
- Tests Python bajo `grams-app/tests/` y tests del MCP Go.
- Documentación de contrato dentro de `openspec/` o junto al componente, si se
  necesita hacer visible la superficie publicada.

## Constraints

- Sólo el Inbox es la fuente durable de eventos; LangGraph no SHALL mantener
  una cola paralela.
- `process_get_active` es una lectura y no SHALL crear jerarquías, procesos ni
  categorías.
- No se SHALL implementar un fallback silencioso a `process_list`, `process_get`
  ni a consultas SQLite para ocultar la incompatibilidad.
- Debe mantenerse la semántica actual de `process_create`, `process_close`,
  estados de proceso y leases del Inbox.
- La implementación no debe modificar esta especificación para justificar que
  el endpoint en ejecución siga sin publicar la herramienta.

## Edge Cases

- `tools/list` correcto pero `tools/call` responde `unknown tool`: tratar como
  incompatibilidad del servidor y fallo operativo, no como ausencia de proceso.
- `tools/list` sin la herramienta: fallo de contrato antes de iniciar una
  operación de lifecycle; no hacer mutaciones.
- Proceso inexistente: resultado `None`, sin excepción y sin crear uno desde
  `get_active_process`.
- Respuesta con proceso de otro proyecto, estado no `ACTIVE`, ID ausente,
  contenido vacío o JSON-RPC inválido: rechazar con error de contrato.
- Timeout después de una lectura: puede reintentarse según la política de
  transporte existente, pero no debe generar una mutación ni confirmar el
  Inbox por asumir que la lectura tuvo éxito.
- El servidor puede devolver el resultado MCP en `structuredContent` o en
  contenido JSON; ambos formatos soportados actualmente SHALL conservar la
  misma semántica validada.

## Error Handling

Los errores de validación de entrada deben detectarse antes de invocar MCP. Los
errores JSON-RPC, `isError`, `unknown tool`, transporte y esquema de respuesta
deben propagarse como errores operativos identificables por herramienta. El
runtime debe registrar el fallo del ciclo, liberar el lease mediante la ruta
normal de Inbox y dejar el evento en `PENDING` o `FAILED` según los intentos;
nunca debe hacer ACK falso. No se deben reintentar mutaciones inexistentes ni
crear un proceso para recuperarse de una lectura incompatible.

## Acceptance Criteria

1. Contra el servidor MCP que se ejecuta en el entorno soportado, `tools/list`
   contiene `process_get_active` y la llamada con `{ "id": "project-1" }` no
   responde `unknown tool`.
2. Una prueba Supervisor -> MCP verifica handshake, `tools/list`, llamada real
   y proceso activo devuelto con el proyecto y estado esperados.
3. La misma prueba verifica que un proyecto sin proceso devuelve `None` y que
   no se ejecuta `process_create` ni otra mutación.
4. Una prueba con un servidor que responde `unknown tool` verifica un error
   que menciona `process_get_active` y no devuelve `None`.
5. Una prueba de runtime con Inbox real o fake verificable demuestra que ese
   error deja los eventos en `PENDING`/`FAILED`, no en `PROCESSED`, y que
   `fail_batch` recibe la causa.
6. Las pruebas de leases, cohortes, reintentos y ACK atómico existentes siguen
   pasando sin cambios de semántica.
7. No existe ningún camino de fallback que consulte SQLite o cree un proceso
   cuando falta la herramienta MCP.

## Test Scenarios

1. Registrar el servidor Go y comprobar mediante `tools/list` nombre, esquema y
   presencia de `process_get_active`.
2. Crear un proyecto y un proceso activo en el backend de prueba; llamar desde
   `MCPMemoryClient` y verificar los argumentos JSON-RPC exactos y el resultado.
3. Consultar un proyecto sin proceso; comprobar `None`, ausencia de error y
   ausencia de mutaciones.
4. Ejecutar contra un fake MCP que no registra la herramienta; comprobar el
   error `unknown tool`, su diagnóstico y que no haya fallback.
5. Ejecutar contra un fake que lista la herramienta pero rechaza la llamada;
   comprobar que una capacidad anunciada no se considera prueba suficiente.
6. Devolver proceso con proyecto incorrecto, estado `CLOSED`, ID ausente y
   payload malformado; comprobar rechazo en el cliente/servicio.
7. Ejecutar un ciclo Supervisor con un evento reclamado y `unknown tool`;
   verificar `PROCESSING -> PENDING`, lease liberado y ausencia de ACK.
8. Repetir hasta `max_attempts`; verificar transición final a `FAILED` y que no
   se creen procesos.
9. Ejecutar las regresiones de `test_read_inbox.py`,
   `test_supervisor_v2_resilience.py` y `test_latest_fixes_regressions.py`,
   además de las pruebas del cliente y proceso existentes.
10. Ejecutar `go test ./...` y la suite Python de Supervisor indicada por el
    repositorio.

## Out of Scope

- Renombrar la API a `process_get` o diseñar aliases permanentes.
- Cambiar estados, relaciones, categorías o reglas de lifecycle de procesos.
- Rediseñar retries del transporte HTTP o la política general de reintentos del
  Inbox.
- Añadir una nueva fuente durable de eventos o cambiar el esquema SQLite.
- Corregir otros nombres de herramientas MCP no relacionados con esta
  incompatibilidad.

## Assumptions

- El nombre canónico solicitado por la arquitectura y el Supervisor es
  `process_get_active`, con argumento `id`; cambiarlo requeriría una decisión
  de contrato distinta y no se asume aquí.
- El comportamiento de ausencia de proceso observado en el handler Go
  (`nil, nil`) es intencional y debe conservarse.
- “Servidor que se ejecuta” significa el binario/entrypoint configurado para el
  Memory MCP independiente; la prueba de integración debe apuntar a ese mismo
  wiring para detectar deriva de artefactos.
- Si la causa real resulta ser un binario obsoleto o un endpoint equivocado, la
  corrección deberá actualizar el proceso de arranque/artefacto o su
  configuración, no introducir lógica de compatibilidad silenciosa en el
  Supervisor.
