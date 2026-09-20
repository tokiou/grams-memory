# Intervención fuerte y no destructiva de OpenCode

## Objective

Modificar el contrato de entrega de una intervención del Supervisor para que,
cuando la decisión sea `INTERVENE`, detenga únicamente la ejecución activa de
la sesión OpenCode objetivo mediante el endpoint de abort de esa sesión y,
solamente después de confirmar esa llamada, entregue la indicación mediante
`prompt_async` en la misma sesión. La sesión, su historial y el servidor
OpenCode deben permanecer vivos; no se debe matar, cerrar, eliminar ni recrear
ningún proceso o sesión.

## Relevant Context

- `grams-app/supervisor/opencode/client.py` ya tiene las operaciones relevantes:
  - `abort_session(session_id)` hace `POST /session/{session_id}/abort`.
  - `send_message(session_id, message)` delega en `inject_context`, que hace
    `POST /session/{session_id}/prompt_async` con una parte de texto.
  - `_session_path` codifica el `session_id` en la URL y `_request` acepta una
    respuesta `2xx` sin cuerpo, incluyendo `204 No Content`.
- `grams-app/supervisor/agent/nodes/send_intervention.py` es actualmente el
  límite determinista de entrega. Valida `root_session_id` y el mensaje,
  persiste una intención en la categoría `EVIDENCE`, y luego usa
  `opencode.send_message`. La intención distingue, entre otros, `PREPARED`,
  `SENDING`, `DELIVERED` y `DELIVERY_UNKNOWN`.
- La topología vigente es
  `BUILD_INTERVENTION -> SEND_INTERVENTION -> RECORD_INTERVENTION -> FINALIZE`.
  `BUILD_INTERVENTION` genera el texto; `SEND_INTERVENTION` no debe decidir de
  nuevo si hay que intervenir; `FINALIZE` hace el ACK del lote sólo si el grafo
  termina correctamente.
- `SupervisorRuntime` marca el lote como fallido/reintentable cuando una
  excepción sale del grafo. La observación operacional de
  `openspec/observations/raman-intervention.md` exige distinguir la decisión de
  intervenir del resultado real de su entrega.
- `SupervisorWorker` usa el mismo `OpenCodeClient` durante toda su vida. El
  cierre de ese cliente (`aclose`) ocurre únicamente durante el shutdown de la
  aplicación en `supervisor/app.py`; no forma parte de una intervención.
- La arquitectura establece que una intervención mantiene el proceso de
  memoria actual y cambia la dirección de la ejecución sin cerrar el proceso.

## Scope

Incluye:

- El contrato de llamadas OpenCode para una intervención fuerte.
- El orden, las precondiciones y los resultados de `abort` y `prompt_async`.
- La representación durable de las fases de entrega y la compatibilidad con
  las intenciones ya persistidas.
- La propagación de errores al ciclo del Supervisor, sin ACK falso.
- Pruebas unitarias del cliente y del nodo, pruebas de orden y pruebas de
  integración del flujo actual.

No incluye:

- Cambiar la política que decide cuándo `Jev` selecciona `INTERVENE`.
- Cambiar el prompt generado por OpenRouter.
- Cerrar o crear procesos de Memory MCP, modificar el Inbox o cambiar el
  protocolo del plugin de eventos.
- Implementar un endpoint nuevo en OpenCode, modificar el servidor OpenCode o
  controlar procesos del sistema operativo.

## Expected Behavior

Para una intervención nueva sobre `root_session_id = S` y el mensaje durable
`M`, el flujo observable debe ser:

```text
persistir intención PREPARED/ABORTING
    -> POST /session/{quote(S)}/abort
    -> confirmar respuesta 2xx del abort
    -> POST /session/{quote(S)}/prompt_async con M
    -> confirmar respuesta 2xx del prompt_async
    -> marcar la intención como DELIVERED
```

Las dos llamadas HTTP deben ser secuenciales y usar exactamente el mismo `S`.
El `abort` afecta sólo la ejecución activa de esa sesión; no elimina la sesión
ni su historial. El `prompt_async` inicia o reanuda la indicación en esa misma
sesión después del abort.

Si el abort no se confirma como exitoso, no se debe llamar a `prompt_async`.
Un abort exitoso por sí solo no es una intervención entregada. Sólo un
`prompt_async` aceptado por OpenCode puede producir `delivered = true`.

Una respuesta `204 No Content` de cualquiera de las dos operaciones cuenta como
éxito de transporte. Esto confirma aceptación de la operación, no que el
agente haya completado la nueva indicación.

Las rutas `CONTINUE`, `NEED_MORE_MEMORY` y `CLOSE_PROCESS` no deben emitir
ninguna de estas llamadas como efecto colateral.

## Functional Requirements

### Contrato del cliente OpenCode

1. `OpenCodeClient.abort_session(session_id)` SHALL realizar un `POST` a
   `/session/{session_id}/abort`, usando el mismo escape de URL que las demás
   operaciones de sesión.
2. La entrega de texto SHALL continuar usando el contrato actual de
   `send_message(session_id, message)` o un equivalente explícito, pero su
   transporte SHALL ser un `POST` a
   `/session/{session_id}/prompt_async` con el cuerpo:

   ```json
   {"parts":[{"type":"text","text":"<mensaje>"}]}
   ```

3. El cliente SHALL considerar exitosas las respuestas HTTP `2xx`, aunque no
   tengan cuerpo. SHALL propagar como error las respuestas no `2xx`, los
   timeouts, las desconexiones y las respuestas HTTP que no se puedan procesar
   según el contrato existente.
4. El cliente SHALL mantener ambas operaciones asíncronas y SHALL no iniciar,
   detener ni cerrar el servidor OpenCode.
5. El cliente SHALL conservar `aclose()` como una operación de lifecycle de la
   aplicación, nunca como parte de `abort_session`, `send_message` o del nodo
   `SEND_INTERVENTION`.

### Orden y alcance de la intervención

6. Para una intención nueva, `SEND_INTERVENTION` SHALL llamar a
   `abort_session(root_session_id)` antes de cualquier llamada de entrega
   `prompt_async` para esa intención.
7. El nodo SHALL esperar (`await`) el resultado del abort antes de enviar el
   prompt. No SHALL usar `asyncio.gather`, tareas concurrentes, callbacks ni
   otra forma de solapar abort y prompt.
8. El nodo SHALL enviar el prompt únicamente después de una respuesta `2xx`
   confirmada del abort. Un error, timeout, respuesta `4xx/5xx` o respuesta
   inválida del abort SHALL impedir el prompt.
9. La llamada de abort y la llamada de prompt SHALL recibir exactamente el
   `root_session_id` de la intervención. No se SHALL sustituir por un nuevo
   session ID, un ID de proceso de memoria, un task ID o un ID de subagente.
10. La implementación SHALL limitar el efecto del abort a la ejecución activa
    de la sesión indicada. No SHALL llamar a APIs de kill de proceso, cerrar la
    sesión, eliminarla, recrearla, reiniciar OpenCode ni detener el servidor.
11. La implementación SHALL conservar la sesión después del abort y usarla
    nuevamente para `prompt_async`; el historial previo debe continuar siendo
    consultable por `get_context`.
12. El nodo SHALL conservar la responsabilidad actual de validar el ID de
    sesión, el mensaje no vacío y `evidence_category_id` antes de realizar
    llamadas OpenCode. Una validación fallida SHALL producir cero llamadas de
    abort y prompt.

### Intención durable y resultados

13. La intención SHALL persistirse antes de la primera llamada OpenCode. La
    implementación SHALL poder distinguir al menos estas fases semánticas:

    | Fase | Significado |
    | --- | --- |
    | `PREPARED` | La intención existe, pero no se ha intentado abortar. |
    | `ABORTING` | El abort fue iniciado y su resultado todavía no está confirmado. |
    | `SENDING` | El abort fue confirmado; el `prompt_async` fue iniciado o su resultado aún no está confirmado. |
    | `DELIVERED` | `prompt_async` terminó con respuesta HTTP `2xx`. |
    | `ABORT_FAILED` o `ABORT_UNKNOWN` | El abort no fue confirmado; nunca se envió el prompt en ese intento. |
    | `DELIVERY_UNKNOWN` | El prompt pudo haber sido aceptado, pero no se pudo confirmar su respuesta. |

    Los nombres de almacenamiento pueden adaptarse a la convención existente,
    pero la distinción y las transiciones SHALL ser observables y verificables.
14. La transición normal SHALL ser
    `PREPARED -> ABORTING -> SENDING -> DELIVERED`. `DELIVERED` sólo se puede
    escribir después del `prompt_async`, nunca después del abort.
15. El resultado de `SEND_INTERVENTION` SHALL conservar los campos actuales
    necesarios para `RECORD_INTERVENTION` y SHALL permitir distinguir, sin
    inferirlo desde texto libre, como mínimo:

    - `delivered`: `true` sólo tras `prompt_async` `2xx`;
    - estado del abort: confirmado, fallido o desconocido;
    - estado del prompt: no intentado, entregado, fallido o desconocido;
    - `session_id`, mensaje durable y clave de entrega.

16. Una intervención con abort confirmado y prompt no confirmado SHALL tener
    `delivered = false`; no se SHALL registrar como entrega exitosa.
17. Las intenciones ya persistidas con `DELIVERED` o `DELIVERY_UNKNOWN` SHALL
    conservar la semántica actual: no se debe volver a llamar a OpenCode sólo
    porque el ciclo se reintente.
18. Una intención en `SENDING` SHALL tratarse como una entrega cuyo abort ya fue
    confirmado. En una recuperación, el nodo debe conservar el mecanismo
    existente de consultar el contexto y buscar el mensaje exacto antes de
    decidir `DELIVERED` o `DELIVERY_UNKNOWN`; no debe enviar un segundo prompt a
    ciegas.
19. Una intención en `ABORTING`, `ABORT_FAILED` o `ABORT_UNKNOWN` SHALL nunca
    permitir un prompt sin una nueva confirmación exitosa del abort. Un reintento
    puede volver a intentar el abort sobre la misma sesión, pero no puede saltar
    directamente a `prompt_async`.
20. El mensaje usado en todas las recuperaciones SHALL ser el mensaje durable de
    la intención existente, no un mensaje recién generado que lo sustituya.

### Compatibilidad con el grafo y el Inbox

21. La topología SHALL conservar las aristas
    `BUILD_INTERVENTION -> SEND_INTERVENTION -> RECORD_INTERVENTION -> FINALIZE`.
    No se SHALL introducir un nodo que mate o recree sesiones.
22. `BUILD_INTERVENTION` SHALL seguir siendo el único generador del texto de la
    intervención. `SEND_INTERVENTION` SHALL seguir siendo determinista y no
    SHALL llamar a Jev ni a OpenRouter.
23. Una decisión `INTERVENE` SHALL mantener el proceso de memoria activo; el
    abort de OpenCode no equivale a `CLOSE_PROCESS`, `SUPERSEDED` ni a otra
    transición del lifecycle de Memory MCP.
24. Si el abort falla de forma conocida o no se puede confirmar, el nodo SHALL
    propagar un error operativo y el runtime SHALL usar la ruta existente de
    `fail_batch`; el lote no SHALL recibir ACK exitoso por esa intervención.
25. Si `prompt_async` falla de forma no ambigua, el resultado SHALL ser no
    entregado y el error SHALL permanecer observable según la política vigente.
    Si la respuesta del prompt se pierde después de emitir la solicitud, el
    nodo SHALL conservar la reconciliación actual: buscar el mensaje en el
    contexto, marcar `DELIVERED` sólo si se observa y, de lo contrario, marcar
    `DELIVERY_UNKNOWN` sin reenviar ciegamente.
26. Un `prompt_async` exitoso SHALL finalizar el ciclo por la ruta existente y
     permitir el ACK normal. Ese ACK significa que el ciclo terminó y que la
     entrega fue aceptada; no significa que la tarea OpenCode haya terminado.
    Un resultado `DELIVERY_UNKNOWN` SHALL NOT pasar por `RECORD_INTERVENTION` ni
    producir ACK hasta que la reconciliación observe el mensaje en el contexto.

## Non-Functional Requirements

- Las llamadas SHALL ser no bloqueantes y compatibles con el cliente async
  existente.
- El orden abort-then-prompt SHALL ser determinista y observable con un fake o
  transport de prueba que registre las llamadas.
- Los reintentos SHALL ser seguros frente a respuestas perdidas: no deben
  producir un prompt duplicado cuando el mensaje ya se observa en el contexto
  ni marcar como entregado un abort aislado.
- Los logs SHALL distinguir las operaciones de abort y `prompt_async`, su
  resultado y duración, sin incluir el cuerpo del mensaje, credenciales ni
  payloads sensibles.
- La intervención no SHALL aumentar el alcance de privilegios del Supervisor:
  sólo puede operar sobre la sesión recibida y los endpoints existentes de
  OpenCode.
- La ausencia de un servidor OpenCode o de una sesión válida SHALL producir un
  fallo observable, no un fallback destructivo ni una sesión sustituta.

## Affected Components

La implementación de esta especificación probablemente afectará:

- `grams-app/supervisor/agent/nodes/send_intervention.py`, para insertar el
  abort confirmado antes del envío y conservar la recuperación durable.
- `grams-app/supervisor/opencode/client.py`, sólo si hace falta exponer con
  mayor claridad el contrato `prompt_async`; `abort_session` y el transporte
  actual ya existen.
- `grams-app/supervisor/agent/state.py` o contratos equivalentes, sólo si se
  necesitan campos explícitos para los estados de abort y prompt.
- `grams-app/tests/test_supervisor_v2_nodes.py`,
  `grams-app/tests/test_supervisor_v2_resilience.py` y una prueba del cliente
  OpenCode, para fakes, orden, errores y respuestas `204`.
- `grams-app/supervisor/observability.py` únicamente si los eventos actuales
  no permiten diferenciar las dos operaciones sin exponer datos sensibles.

No se espera modificar `graph.py`, `SupervisorWorker`, el endpoint `POST
/events` ni el lifecycle de `supervisor/app.py`.

## Constraints

- SQLite Inbox sigue siendo la fuente durable de eventos; no se debe crear una
  cola de intervenciones en memoria.
- Memory MCP sigue siendo la fuente durable de la intención y de la evidencia
  de entrega. El estado LangGraph sólo coordina el ciclo actual.
- Deben conservarse `root_session_id`, la clave de ciclo y la semántica de
  deduplicación/reconciliación ya existente.
- Se debe usar el cliente inyectado; el nodo no puede construir URLs HTTP ni
  abrir un cliente paralelo.
- `get_context` sólo puede utilizarse para la reconciliación existente de una
  entrega cuyo prompt ya fue iniciado; no sustituye al abort ni permite enviar
  el prompt antes del abort.
- No se debe interpretar un abort como cierre de sesión, cierre de proceso de
  memoria o finalización de la tarea.
- El servidor OpenCode debe seguir ejecutándose para recibir el prompt posterior
  y para atender otros ciclos.

## Edge Cases

- `root_session_id` vacío, mensaje vacío o categoría de evidencia ausente:
  error de validación y cero llamadas externas.
- `session_id` con `/`, espacios u otros caracteres reservados: ambas rutas
  deben usar el mismo ID codificado y no deben truncarlo.
- Abort `204` sin cuerpo: continuar al prompt.
- Prompt `204` sin cuerpo: marcar la entrega como aceptada, no esperar un texto
  de respuesta.
- Abort `404`, `409`, `429`, `5xx`, timeout o desconexión: no enviar prompt;
  distinguir fallo conocido de resultado desconocido y no marcar `DELIVERED`.
- La sesión puede haber quedado inactiva justo antes del abort. Sólo una
  respuesta `2xx` del endpoint, incluyendo un eventual no-op documentado por
  OpenCode, permite continuar; un `404/409` no se debe reinterpretar como
  éxito.
- El abort puede haber tenido efecto remoto antes de perderse la respuesta.
  La recuperación debe volver a confirmar el abort antes de enviar el prompt;
  nunca debe saltar de un estado incierto a `prompt_async`.
- El prompt puede haber sido aceptado antes de perderse la respuesta. La
  recuperación debe consultar el contexto y no duplicar el prompt a ciegas.
- El prompt puede ser rechazado después de un abort exitoso: la sesión debe
  permanecer viva, el resultado debe ser no entregado y el error debe quedar
  visible.
- La memoria puede fallar al persistir `ABORTING`, `SENDING` o `DELIVERED`.
  Nunca se debe confirmar una entrega cuyo estado durable no puede verificarse;
  el ciclo debe seguir la política vigente de fallo/reintento.
- Un reintento puede contener un mensaje generado distinto; la intención
  durable existente conserva precedencia, igual que en el flujo actual.
- Dos eventos o dos ciclos no deben provocar una recreación de sesión ni
  mezclar sus claves de entrega.
- Un ciclo `CONTINUE` con el mismo `root_session_id` no debe abortar una sesión
  por haber existido una intervención en un ciclo anterior.

## Error Handling

1. Los errores de validación deben producirse antes de `abort_session` y
   `prompt_async`.
2. Antes de llamar a OpenCode se debe haber creado o localizado una intención
   durable válida. Si esa operación falla, no se debe llamar a abort.
3. Si el abort devuelve un error conocido, el nodo debe persistir un estado de
   abort fallido cuando sea posible, propagar la excepción y no llamar al
   prompt. El runtime debe liberar/reencolar el lote mediante `fail_batch`; no
   debe ACKearlo como finalizado.
4. Si el resultado del abort es incierto por timeout, desconexión o pérdida de
   respuesta, se debe conservar un estado incierto y aplicar la misma regla
   conservadora: no prompt hasta una nueva confirmación exitosa del abort.
5. Si `prompt_async` falla después de un abort confirmado, la sesión no debe
   cerrarse ni recrearse. El error debe distinguirse de un fallo del abort y no
   debe producir `delivered = true`.
6. Si la respuesta del prompt es ambigua, el mecanismo de reconciliación puede
   terminar en `DELIVERY_UNKNOWN`, pero nunca puede convertir la ambigüedad en
   `DELIVERED` sin evidencia de aceptación o del mensaje en el contexto.
7. Ninguna excepción de estas operaciones debe llamar a `OpenCodeClient.aclose`,
   detener `SupervisorWorker`, finalizar FastAPI ni matar un proceso externo.
8. Los errores registrados deben incluir operación, sesión de forma segura,
   fase y tipo de error, pero no el texto completo de la intervención ni
   secretos.

## Acceptance Criteria

1. Para una decisión `INTERVENE` nueva, un fake de OpenCode registra exactamente
   `abort_session(S)` seguido de `send_message(S, M)`; no registra llamadas
   concurrentes, una tercera sesión ni otra operación destructiva.
2. Un transport fake del cliente observa, en orden, `POST
   /session/{quote(S)}/abort` y `POST /session/{quote(S)}/prompt_async`, con el
   cuerpo de texto correcto en la segunda llamada.
3. Las respuestas `204` del abort y de `prompt_async` son aceptadas; el
   resultado final tiene `delivered = true` sólo después de la segunda.
4. Si el abort devuelve un error, timeout o desconexión, el fake confirma que
   `prompt_async` no fue llamado, la intención no está `DELIVERED` y el ciclo
   no hace ACK exitoso.
5. Si `prompt_async` falla después de un abort exitoso, el resultado no se marca
   como entregado, la sesión continúa disponible y el retry conserva la
   reconciliación actual sin reenviar ciegamente.
6. Una intención existente `DELIVERED` o `DELIVERY_UNKNOWN` no vuelve a llamar a
   abort ni a prompt; una intención en una fase de abort incierta nunca llama a
   prompt sin confirmar primero otro abort.
7. El `root_session_id` permanece idéntico antes y después de la intervención,
   `get_context(S)` sigue pudiendo consultarse y no se invoca ninguna API de
   creación, eliminación o cierre de sesión/servidor.
8. Las rutas `CONTINUE`, `NEED_MORE_MEMORY` y `CLOSE_PROCESS` siguen pasando sin
   abortar OpenCode; la topología y el lifecycle de Memory MCP permanecen sin
   cambios.
9. Los logs o eventos de observabilidad distinguen el resultado del abort del
   resultado de `prompt_async`, no confunden “decisión de intervenir” con
   “entrega confirmada” y no exponen el mensaje ni credenciales.
10. Las pruebas existentes de Inbox, runtime y lifecycle continúan pasando,
    incluyendo la regla de que un error obligatorio del grafo no genera ACK
    falso.

## Test Scenarios

1. **Orden normal:** ejecutar `SEND_INTERVENTION` con un fake que registre
   llamadas y verificar `abort -> prompt_async`, mismo ID de sesión y mensaje
   durable.
2. **HTTP del cliente:** usar `httpx.MockTransport` para comprobar métodos,
   rutas con un ID que requiera escape, cuerpo de `prompt_async` y aceptación de
   `204` en ambas operaciones.
3. **Abort rechazado:** devolver `404`, `409` y `500` desde `/abort`; comprobar
   cero llamadas a `/prompt_async`, estado no entregado y propagación del error.
4. **Abort inalcanzable:** provocar timeout/desconexión y comprobar que el
   ciclo no envía el prompt ni cierra el cliente o el servidor.
5. **Abort con respuesta perdida:** completar el efecto del abort pero perder la
   respuesta; reintentar y verificar que el prompt sólo ocurre después de una
   nueva confirmación del abort.
6. **Prompt aceptado sin cuerpo:** devolver `204` después del abort y verificar
   `DELIVERED`, `delivered = true` y que la sesión sigue siendo la misma.
7. **Prompt con respuesta perdida:** hacer que el fake registre el prompt y
   lance un error; en el retry hacer que `get_context` contenga el mensaje y
   verificar que no se envía un segundo prompt. Repetir con contexto sin el
   mensaje y comprobar `DELIVERY_UNKNOWN`, no `DELIVERED`.
8. **Fallos de persistencia:** fallar al crear la intención o al actualizar la
   fase `ABORTING` y verificar que no se llama a OpenCode; fallar al persistir
   `DELIVERED` y verificar que no se confirma silenciosamente la entrega.
9. **Estados preexistentes:** comprobar que `DELIVERED` y
   `DELIVERY_UNKNOWN` no repiten llamadas, y que `SENDING` usa la reconciliación
   existente sin enviar a ciegas.
10. **Rutas no interventoras:** ejecutar las rutas `CONTINUE`, `NEED_MORE_MEMORY`
    y `CLOSE_PROCESS` con un fake que falle si recibe abort o prompt.
11. **Runtime/Inbox:** propagar un fallo de abort a través de
    `SupervisorRuntime` y verificar `fail_batch`/reintento, ausencia de ACK y
    conservación de los leases según la política existente.
12. **No destrucción:** usar spies sobre `aclose`, creación/eliminación de
    sesiones, kill de procesos, `SupervisorWorker.stop` y shutdown de FastAPI;
    comprobar que ninguno se ejecuta durante una intervención.
13. **Compatibilidad end-to-end:** recorrer
    `BUILD_INTERVENTION -> SEND_INTERVENTION -> RECORD_INTERVENTION -> FINALIZE`
    con respuestas exitosas y verificar que el registro de evidencia conserva
    la clave de entrega y el estado de aceptación.

## Out of Scope

- Elegir o recalibrar los umbrales que producen `INTERVENE`.
- Alterar la semántica de `prompt_async` para convertirlo en una llamada
  síncrona o esperar la finalización del agente.
- Reemplazar OpenCode por otro agente, crear una sesión paralela o migrar el
  historial a otra sesión.
- Finalizar la tarea, cerrar el proceso de memoria o borrar evidencia por el
  hecho de abortar una ejecución activa.
- Cambiar el plugin de observación, el envelope `POST /events`, SQLite Inbox,
  Memory MCP, Harbor o el servidor OpenCode.
- Crear retries distribuidos, una nueva cola durable o una reconciliación
  global de sesiones fuera del mecanismo actual.

## Assumptions

- El endpoint existente `POST /session/{id}/abort` es la operación soportada
  por OpenCode para detener la ejecución activa de una sesión sin eliminar la
  sesión. La implementación no debe sustituirlo por una operación de proceso.
- Una respuesta `2xx` del endpoint significa que OpenCode aceptó la operación;
  una respuesta `204` no contiene un resultado adicional que deba validarse.
- Si OpenCode documenta un no-op `2xx` cuando la sesión ya está inactiva, ese
  resultado cuenta como abort confirmado y permite enviar el prompt. Sin esa
  respuesta `2xx`, el Supervisor debe ser conservador y no enviar.
- `send_message` seguirá siendo el adaptador compatible que materializa
  `prompt_async`; si se añade un método con nombre explícito, debe conservar la
  misma ruta, cuerpo y semántica.
- La auditoría de intención existente en `EVIDENCE` puede extender sus estados
  sin cambiar la categoría ni romper la búsqueda por `cycle_key`.
- “Entrega confirmada” significa aceptación de `prompt_async`, no ejecución
  completa ni éxito de la tarea solicitada al agente.

## Ambiguity Requiring Confirmation

El contrato de OpenCode del repositorio no documenta explícitamente qué código
devuelve `/abort` cuando no hay una ejecución activa. Esta especificación adopta
una regla segura y verificable: sólo una respuesta `2xx` permite continuar; un
`404` o `409` no se puede convertir en éxito por inferencia. Si el servidor
OpenCode establece una semántica distinta, debe documentarse y cubrirse con una
prueba de contrato antes de relajar esta regla.
