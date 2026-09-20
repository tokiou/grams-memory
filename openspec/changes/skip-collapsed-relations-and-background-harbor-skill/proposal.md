# Omitir relaciones colapsadas y lanzar trials Harbor en segundo plano

## Objective

Evitar que una relación válida en su forma declarada haga fallar un ciclo cuando
sus endpoints distintos se resuelven al mismo ID por deduplicación, omitiéndola
sin llamar a Memory MCP y registrando el motivo en el resultado. Además, agregar
una skill local de OpenCode que lance trials Harbor siempre con timeout
multiplier `1.0`, en segundo plano y con evidencia suficiente para consultar su
estado posteriormente en el mismo chat.

## Relevant Context

- `validate_memory_proposal` en `grams-app/supervisor/agent/schemas.py` rechaza
  actualmente una relación cuyo `source_id` literal es igual a `target_id`.
- `apply_memory_update.py` resuelve candidatos por la identidad normalizada
  `(category, title.strip().casefold(), content.strip().casefold())`. Hoy aborta
  todo el update si dos endpoints distintos terminan en el mismo ID.
- El resultado vigente ya expone `created_relations`, `skipped_relations` y
  `resolved_refs`, pero no distingue una relación omitida por colapso.
- Este cambio sustituye sólo la política de rechazo post-deduplicación definida
  en `openspec/changes/apply-memory-update-self-edge/proposal.md`; mantiene sus
  garantías de preflight, alcance, idempotencia y rechazo de self-relations
  explícitas.
- `scripts/run_harbor_supervised.sh` elimina un argumento
  `--timeout-multiplier`, pero permite que `HARBOR_TIMEOUT_MULTIPLIER` cambie el
  valor efectivo y ejecuta Harbor en primer plano. `AGENTS.md` aún documenta
  `2.0`, mientras el valor por defecto del wrapper es `1.0`.
- Existe `.opencode/skills/status-supervisor/SKILL.md`, que consulta procesos,
  contenedores, Inbox y logs, pero no existe una skill local para lanzar trials.

## Scope

Incluye la omisión y trazabilidad de relaciones colapsadas por deduplicación,
pruebas de regresión, y una skill local bajo `.opencode/skills/` para lanzar el
wrapper Harbor en background con multiplier `1.0` y reportar PID/log. Incluye
alinear la documentación operativa directamente contradictoria.

No incluye aceptar self-relations explícitas, relajar la validación del servidor
Memory MCP, cambiar otros errores de propuesta, crear un gestor general de jobs
ni modificar la skill existente de status salvo que sea imprescindible para
consumir la evidencia producida por la nueva skill.

## Expected Behavior

1. Una relación con endpoints declarados distintos que, tras resolver refs y
   deduplicar memorias, apunta al mismo ID se omite; las demás memorias y
   relaciones válidas del update continúan aplicándose.
2. La relación omitida no llega a `memory.link` y queda identificada en
   `memory_update_result`, incluyendo relación original, ID resuelto y motivo.
3. Una relación declarada con endpoints literalmente iguales sigue fallando en
   `validate_memory_proposal`, antes de cualquier escritura.
4. Al pedir lanzar un trial, la nueva skill usa el wrapper del repositorio,
   fuerza multiplier `1.0`, inicia un proceso desacoplado en segundo plano y
   devuelve control al chat con PID y ruta del log. Una consulta posterior de
   status usa esos datos y la skill de status existente para informar evidencia
   viva sin iniciar otro trial.

## Functional Requirements

### Relaciones de memoria

1. `validate_memory_proposal` SHALL seguir rechazando toda relación donde los
   valores declarados `source_id` y `target_id` sean iguales.
2. `apply_memory_update` SHALL diferenciar una self-relation explícita de una
   igualdad producida sólo después de resolver dos endpoints distintos.
3. Una igualdad post-resolución SHALL omitir únicamente esa relación, SHALL
   realizar cero llamadas `memory.link` para ella y SHALL continuar con el resto
   del update. No SHALL convertir el ciclo en fallo por ese motivo.
4. El resultado SHALL añadir `omitted_relations`, una lista (vacía cuando no
   haya omisiones) con una entrada por relación colapsada. Cada entrada SHALL
   contener `source_id`, `relation_type` y `target_id` originales,
   `resolved_id`, y `reason` con el valor estable
   `deduplication_self_relation`.
5. `skipped_relations` SHALL conservar su contrato actual para relaciones que
   ya existían; `omitted_relations` SHALL ser la trazabilidad separada para este
   caso y SHALL mantener el orden de la propuesta.
6. Los errores de esquema, alcance, endpoint desconocido, conflicto durable y
   MCP SHALL conservar su comportamiento actual. Memory MCP SHALL continuar
   rechazando cualquier self-edge que alcance su frontera.

### Skill de Harbor

7. SHALL existir una skill de proyecto en
   `.opencode/skills/run-harbor-trial/SKILL.md`, con frontmatter válido, nombre
   coincidente con el directorio y descripción que la active al solicitar
   lanzar/ejecutar un trial Harbor supervisado.
8. La skill SHALL ejecutar `scripts/run_harbor_supervised.sh` desde la raíz del
   repositorio y SHALL fijar explícitamente `HARBOR_TIMEOUT_MULTIPLIER=1.0`, sin
   permitir que el ambiente o un argumento del usuario produzcan otro valor.
9. La ejecución SHALL quedar en segundo plano sin bloquear el turno hasta que
   finalice el trial. La skill SHALL capturar stdout/stderr en un log local y
   SHALL conservar el PID del proceso lanzado.
10. Tras verificar que el proceso arrancó o detectar un fallo inmediato, la
    respuesta SHALL indicar outcome inicial, PID cuando exista, log y el
    multiplier efectivo. Un fallo de arranque SHALL mostrarse y no SHALL
    reportarse como trial activo.
11. Ante una consulta posterior de status en el mismo chat, SHALL reutilizarse
    el PID/log reportado y el procedimiento de `status-supervisor`; no SHALL
    iniciarse otro trial ni esperarse la terminación del proceso para responder.
12. La documentación del perfil local SHALL dejar de indicar `2.0` como valor
    estándar y SHALL reflejar el valor obligatorio `1.0` para este flujo.

## Non-Functional Requirements

- La omisión SHALL ser determinista e idempotente para la misma propuesta y
  contexto, sin llamadas adicionales a MCP.
- Logs, PID y respuestas de status no SHALL exponer credenciales de `.env`.
- La skill SHALL usar rutas derivadas de la raíz del repositorio y no depender
  del directorio desde el que OpenCode fue iniciado.

## Affected Components

- `grams-app/supervisor/agent/nodes/apply_memory_update.py`.
- Pruebas de nodos/regresión bajo `grams-app/tests/`.
- Nueva `.opencode/skills/run-harbor-trial/SKILL.md`.
- `scripts/run_harbor_supervised.sh` sólo si es necesario para garantizar que
  ningún override cambie `1.0`.
- `AGENTS.md` o documentación equivalente del perfil de trial.

## Constraints

- No se SHALL escribir directamente en SQLite de Memory MCP ni cambiar el
  contrato de `memory.link`.
- Las relaciones sólo pueden usar tipos y endpoints dentro del alcance ya
  permitido.
- La skill SHALL reutilizar el wrapper y la skill de status existentes, no
  duplicar su lógica de diagnóstico.
- Sólo puede ejecutarse un trial a la vez por el mapeo fijo del puerto `4096`.

## Edge Cases

- Dos refs distintas deduplican contra una memoria existente o contra el mismo
  grupo nuevo.
- Una propuesta contiene simultáneamente una relación colapsada, una válida y
  una ya existente.
- Varias relaciones colapsan al mismo ID; todas se trazan en orden y ninguna se
  enlaza.
- Una self-relation explícita usa el mismo ID existente o el mismo `new_N` en
  ambos extremos; sigue siendo error de esquema.
- Harbor falla inmediatamente, existe un trial previo, el log aún está vacío o
  el proceso termina antes de una consulta de status.
- El usuario o el ambiente intentan proporcionar multiplier distinto de `1.0`.

## Error Handling

- Una relación colapsada por deduplicación es una omisión exitosa trazable, no
  una excepción ni un error de MCP.
- Cualquier otro fallo conserva la propagación y semántica de fail/ACK vigente.
- La skill SHALL comprobar el conflicto de trial/puerto antes de lanzar cuando
  sea observable y SHALL informar fallos inmediatos con la ruta del log.
- Status SHALL distinguir proceso activo, finalizado y evidencia insuficiente;
  no SHALL inferir éxito sólo por haber obtenido un PID.

## Acceptance Criteria

1. Dos endpoints distintos que resuelven al mismo ID no hacen fallar el nodo,
   no generan `memory.link` y producen exactamente una entrada
   `omitted_relations` con originales, `resolved_id` y motivo estable.
2. En una propuesta mixta, memorias y relaciones válidas se aplican, las ya
   existentes permanecen en `skipped_relations` y las colapsadas aparecen sólo
   en `omitted_relations`.
3. Una relación con endpoints explícitamente iguales sigue siendo rechazada por
   el validador antes de toda mutación.
4. La skill local es descubrible por OpenCode y todo lanzamiento que genera usa
   el wrapper, multiplier efectivo `1.0` y background, devolviendo PID y log sin
   bloquear hasta el final.
5. Una petición de status posterior puede informar el mismo trial desde
   proceso/log/contenedores/Inbox sin lanzar un segundo trial.
6. Las pruebas automatizadas relevantes pasan y `git diff --check` no reporta
   errores.

## Test Scenarios

1. Unitario: dos candidatos equivalentes enlazados entre sí, con y sin memoria
   existente; verificar éxito, cero links y trazabilidad completa.
2. Unitario: update mixto con relación colapsada, relación nueva y relación ya
   existente; verificar las tres clasificaciones y escrituras esperadas.
3. Unitario de esquema: `m1 -> m1` y `new_1 -> new_1`; verificar rechazo y cero
   mutaciones.
4. Regresión: endpoint fuera de alcance, conflicto de cycle tag y fallo MCP
   siguen fallando según el contrato vigente.
5. Skill/launcher: con un wrapper falso que registra argumentos y ambiente,
   solicitar multiplier distinto y definir un override ambiental; verificar
   valor efectivo `1.0`, retorno rápido, PID y captura combinada en log.
6. Integración operativa: lanzar un trial corto, pedir status en el mismo chat y
   comprobar que se consulta el PID/log original y no aparece un segundo
   proceso Harbor.
7. Fallo inmediato: hacer que el wrapper termine no-cero; verificar diagnóstico,
   log disponible y ausencia de afirmación de trial activo.

## Out of Scope

- Omitir relaciones por tipo inválido, endpoint desconocido o fuera de alcance.
- Cambiar la deduplicación de memorias, introducir transacciones MCP o aceptar
  self-edges en el servidor Go.
- Ejecución concurrente de trials, dashboard, daemon de jobs o historial remoto.
- Cambiar la política de decisiones del Supervisor.

## Assumptions

- “Trazabilidad en el resultado” significa una colección explícita y estable en
  `memory_update_result`; se usa un campo nuevo para no alterar el significado
  de `skipped_relations`.
- “En el mismo chat” significa que OpenCode devuelve control tras el arranque y
  conserva PID/log en el contexto de la conversación; no implica persistencia
  entre chats o reinicios de OpenCode.
- El multiplier obligatorio aplica a trials lanzados mediante la nueva skill.
  Otros usos manuales del wrapper quedan fuera salvo el endurecimiento mínimo
  necesario para que la skill no pueda ser sobreescrita.
