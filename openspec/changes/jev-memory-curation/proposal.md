# Curación supervisada de memoria con JEV y DeepSeek

## Objective

Implementar, en la rama `feat/jev-memory-curation`, el flujo de memoria
supervisada en el que DeepSeek extrae candidatos factuales trazables al Inbox,
JEV decide cuáles conservar y les asigna metadata estructurada, y DeepSeek
genera únicamente el texto final de cada memoria conservando esa metadata. La
persistencia seguirá usando las operaciones MCP ya existentes, sin migración de
SQLite en esta fase. Las decisiones de intervención deberán quedar justificadas
por IDs de memorias de evidencia y códigos de razón, sin alterar inicialmente
los umbrales ni la política de supervisión existentes.

## Relevant Context

### Requisitos explícitos de este cambio

- DeepSeek debe extraer candidatos factuales junto con evidencia y provenance.
- JEV debe curar cada candidato con `keep`, `category`, `role`, `status`,
  `confidence`, `progress_effect`, `importance` y `relations`.
- DeepSeek debe materializar las memorias finales sin poder modificar la
  metadata decidida por JEV.
- La escritura debe realizarse mediante las APIs MCP actuales y sin migración
  inicial.
- Una decisión `INTERVENE` debe incluir `evidence_memory_ids` y
  `reason_codes`.
- `BUILD_INTERVENTION` debe recibir la decisión, las razones, las memorias
  seleccionadas y el summary.
- El Inbox debe conservarse como fuente durable de eventos.
- La implementación debe incluir pruebas y criterios de aceptación por fases.
- No se deben cambiar inicialmente los umbrales ni la política.

### Comportamiento y arquitectura observados

- `docs/v2/JEV_OPENROUTER.md` y `ARCHITECTURE.md` separan las
  responsabilidades: JEV toma decisiones estructuradas; DeepSeek/OpenRouter
  genera texto; Python/LangGraph controla validación, orden y efectos
  secundarios.
- El grafo actual contiene `EXTRACT_MEMORY_UPDATE -> APPLY_MEMORY_UPDATE ->
  LOAD_PROCESS_CONTEXT -> SUPERVISION_DECISION`. La extracción actual permite
  que OpenRouter proponga directamente `title`, `content`, categoría y
  relaciones; este cambio debe separar esa propuesta en extracción factual,
  curación y materialización.
- `SUPERVISION_DECISION` ya ejecuta diagnósticos y una decisión de acción en
  JEV. Sus acciones actuales son `CONTINUE`, `NEED_MORE_MEMORY`, `INTERVENE` y
  `CLOSE_PROCESS`, y existen umbrales configurables para confianza, contexto y
  outcome.
- `BUILD_INTERVENTION` ya es el paso de generación del mensaje después de que
  JEV selecciona `INTERVENE`; actualmente recibe el estado canónico y los
  diagnósticos, pero no recibe una selección explícita de memorias ni códigos
  de razón.
- El `MemoryClient` Python expone `create`, `update`, `link`, `get`, `search`,
  `neighbors` y `expand`. El servidor Go expone `memory_create`,
  `memory_update`, `memory_link` y la jerarquía de procesos con las categorías
  `STRATEGY`, `EVIDENCE` y `SUMMARY`.
- La API actual de `memory_create` admite `category_id`, `content`, `title`,
  `description`, `type`, `status`, `graph_tier`, `avoid`, `confidence` y
  `source`. `memory_link` admite relación, confianza, fuerza de evidencia,
  `direct` y `source`. No existen campos tipados separados para
  `progress_effect`, `importance` o provenance.
- El Inbox persiste los eventos en SQLite y el flujo durable es
  `claim_pending -> PROCESSING -> ack -> PROCESSED`, o `fail -> PENDING/FAILED`.
  LangGraph puede conservar DTOs durante una invocación, pero no puede crear
  una segunda cola durable de eventos.

## Scope

### Incluido

- Contratos y validadores para candidatos factuales, decisiones de curación y
  materializaciones finales.
- Separación de los pasos DeepSeek `EXTRACT_MEMORY_CANDIDATES` y
  `MATERIALIZE_MEMORIES` del paso de curación JEV.
- Propagación inmutable de la metadata JEV hasta la persistencia.
- Mapeo de la metadata al contrato MCP actual, incluido un envelope versionado
  en `description` para los atributos que no tienen columna tipada.
- Aplicación determinista de memorias conservadas y relaciones, con las
  validaciones de scope, deduplicación e idempotencia ya existentes.
- Inclusión de `evidence_memory_ids` y `reason_codes` en la decisión
  `INTERVENE`.
- Selección determinista de las memorias y del summary que recibe
  `BUILD_INTERVENTION`.
- Pruebas unitarias, de topología, de contrato MCP y de ciclo completo Inbox.

### No incluido

- Cambios de umbrales JEV, señales, opciones de acción o política de decisión.
- Nuevas tablas, columnas, migraciones, herramientas MCP o acceso directo a
  SQLite desde el Supervisor.
- Cambios al contrato `POST /events`, al modelo de leases o a la semántica de
  ACK/fail del Inbox.
- Un agente adicional de memoria, una memoria durable paralela o herramientas
  entregadas a cualquiera de los modelos.
- Rediseño del lifecycle de procesos, de `SUMMARY` al cerrar un proceso o de
  la entrega de intervención ya existente.
- Uso de `importance` o `progress_effect` como nuevos umbrales o disparadores
  de intervención.

## Expected Behavior

### Flujo de memoria para un proceso existente

Para un lote de eventos que permanece en el mismo proceso, el flujo deberá ser:

```text
READ_INBOX
  -> ENSURE_ACTIVE_PROCESS
  -> LOAD_PROCESS_CONTEXT
  -> ASSESS_PROCESS_CONTINUITY [JEV]
  -> EXTRACT_MEMORY_CANDIDATES [DeepSeek]
  -> CURATE_MEMORY_CANDIDATES [JEV]
  -> MATERIALIZE_MEMORIES [DeepSeek]
  -> APPLY_MEMORY_UPDATE [Python + MCP]
  -> LOAD_PROCESS_CONTEXT
  -> SUPERVISION_DECISION [JEV]
```

Un pivot de proceso conserva el lifecycle actual: se genera/persiste el
summary, se cierra el proceso, se crea el sucesor y se vuelve a cargar su
contexto antes de extraer candidatos.

Sólo los candidatos con `keep=true` se materializan y persisten. Un candidato
con `keep=false` no puede producir una llamada `memory_create` ni originar una
relación.

### Candidatos factuales

DeepSeek recibe el objetivo, el contexto actual y los eventos finales
normalizados del ciclo. Devuelve candidatos intermedios, no memorias listas para
persistir. Cada candidato debe contener, como mínimo:

```text
candidate_ref: new_N
fact: texto factual acotado
evidence:
  - event_id
    excerpt
provenance:
  source_event_ids
  source_event_types
  cycle_id
```

Los `event_id` deben pertenecer a los eventos reclamados en el ciclo. El
extractor no asigna la categoría final, no decide `keep`, no decide una acción
de supervisión y no escribe en MCP. Los candidatos no pueden contener hechos
que no tengan al menos una referencia de evidencia del Inbox.

`candidate_ref` es local al ciclo y debe ser único, secuencial y estable para
una repetición del mismo ciclo. El texto de evidencia y los excerpts son DTOs
acotados para el modelo; el evento original continúa viviendo únicamente en el
Inbox durable.

### Curación JEV

JEV recibe los candidatos junto con el contexto canónico del proceso y devuelve
una decisión por candidato. Cada decisión debe contener todos estos campos:

```text
candidate_ref
keep: boolean
category: STRATEGY | EVIDENCE
role: MemoryType existente del MCP
status: MemoryStatus existente del MCP
confidence: número en [0, 1]
progress_effect: POSITIVE | NEGATIVE | NEUTRAL | UNCLEAR
importance: número en [0, 1]
```

La respuesta también contiene `relations`. Cada relación debe indicar:

```text
source_ref: candidate_ref o memory_id existente
relation_type: relación permitida por el MCP actual
target_ref: candidate_ref o memory_id existente
```

Si el adaptador JEV devuelve metadata opcional de una relación, como
`confidence`, `evidence_strength` o `direct`, debe validarla y conservarla.
Los tipos de relación válidos son los ya permitidos por Memory MCP:

```text
SUPPORTS, CONTRADICTS, TESTED_BY, PRODUCED, SUCCEEDED_WITH,
FAILED_BECAUSE, BLOCKED_BY, DEPENDS_ON, SUPERSEDES, VALIDATES
```

Una relación sólo puede apuntar a un candidato que se conserva o a una memoria
existente validada dentro del mismo proceso/proyecto. Relaciones hacia
candidatos descartados, referencias desconocidas, auto-relaciones o relaciones
cross-project deben rechazarse antes de cualquier escritura.

Los valores de `confidence`, `progress_effect` e `importance` son metadata de
curación. No deben modificar por sí mismos el routing, la política ni los
umbrales existentes. `keep` es una decisión explícita de JEV, no un resultado
de comparar `importance` contra un umbral nuevo.

### Materialización DeepSeek

DeepSeek recibe únicamente los candidatos conservados y su decisión de
curación como contexto de solo lectura. Para cada `candidate_ref` conservado
devuelve exactamente una materialización:

```text
candidate_ref
title
content
```

La salida no puede incluir `category`, `role`, `status`, `confidence`,
`progress_effect`, `importance`, `relations` ni otros campos de metadata. El
validador debe rechazar campos extra, candidatos duplicados, candidatos
faltantes, texto vacío o referencias no conservadas.

La capa determinista une la materialización textual con la decisión JEV. No
debe inferir metadata a partir de `title` o `content`, aceptar una corrección
del modelo, ni volver a pedir a DeepSeek que clasifique la memoria. Si
DeepSeek intenta devolver metadata, el ciclo falla antes de MCP.

### Persistencia sin migración inicial

La persistencia debe usar exclusivamente las operaciones del `MemoryClient` y
las herramientas MCP actuales:

- `category` se traduce al `category_id` `STRATEGY` o `EVIDENCE` del proceso
  activo.
- `role` se traduce al campo MCP `type` usando los valores `MemoryType`
  existentes.
- `status` se traduce al campo MCP `status` sin normalizarlo a otro estado.
- `confidence` se envía al campo MCP `confidence` sin recalcularlo.
- `title` y `content` provienen exclusivamente de la materialización
  validada.
- `relations` se resuelven a IDs reales después de crear/reutilizar las
  memorias y se persisten mediante `memory_link`.
- `source` debe identificar la escritura como proveniente del Supervisor.

Para conservar los datos que el MCP actual no tiene como campos tipados, cada
memoria nueva debe llevar en `description` un envelope JSON versionado y
acotado. El envelope debe incluir, como mínimo, `candidate_ref`, provenance,
`progress_effect`, `importance` y la versión del contrato de curación. Debe
contener también la metadata JEV completa o referencias suficientes para
verificar que no fue modificada. El prefijo de idempotencia del ciclo debe
conservarse antes del envelope para que los reintentos puedan reconocer una
propuesta ya persistida. La representación exacta debe ser estable y
documentada por el validador, no texto libre generado por DeepSeek.

Las memorias legacy sin envelope siguen siendo legibles. No se debe modificar
su metadata para adaptarlas. Si una deduplicación encuentra una memoria con el
mismo contenido pero metadata incompatible con la decisión JEV, la operación
debe fallar de forma explícita en vez de actualizarla silenciosamente.

No se deben añadir columnas, modificar `001_initial.sql`, crear una nueva
herramienta MCP ni acceder directamente a la base Go. Una migración tipada de
los atributos del envelope es una fase posterior y queda fuera de este cambio.

### Intervención condicionada por memoria

La salida de `SUPERVISION_DECISION` mantiene las acciones y los umbrales
actuales. Cuando la acción sea `INTERVENE`, la decisión debe contener:

```text
evidence_memory_ids: lista no vacía de IDs de memorias EVIDENCE
reason_codes: lista no vacía de códigos controlados
```

Los IDs deben pertenecer al contexto actual o a una expansión ya validada del
mismo proceso/proyecto. No se deben aceptar IDs arbitrarios proporcionados por
el modelo. Los códigos son explicaciones estructuradas de la decisión, no
nuevos detectores ni rutas. Inicialmente deben reutilizar los motivos ya
representados por la arquitectura, por ejemplo `POSSIBLE_PROGRESS_STALL`,
`POSSIBLE_RESEARCH_LOOP`, `POSSIBLE_HYPOTHESIS_OSCILLATION`,
`DELIVERABLE_MISSING`, `VALIDATION_MISSING`, `REPEATED_FAILURE` y
`OBJECTIVE_DRIFT`. La lista final de códigos debe estar centralizada en el
validador; no se deben aceptar strings libres.

La validación debe rechazar una decisión `INTERVENE` sin evidencia o sin
razones. No debe convertirla automáticamente en `CONTINUE` ni inventar
evidencia para hacerla válida.

### Contrato de `BUILD_INTERVENTION`

Antes de llamar a DeepSeek, el grafo debe resolver determinísticamente los IDs
seleccionados y cargar sus DTOs. `BUILD_INTERVENTION` debe recibir un payload
que contenga explícitamente:

```text
decision: decisión completa de SUPERVISION_DECISION
reason_codes: códigos de razón validados
evidence_memory_ids: IDs seleccionados
selected_memories: memorias correspondientes a esos IDs
summary: summary actual del proceso, o null si no existe
```

Puede incluir además el objetivo, el proceso actual, los diagnósticos y la
ejecución reciente necesarios para redactar el mensaje, pero no debe incluir
como si fueran evidencia memorias no seleccionadas. DeepSeek sólo genera el
texto de intervención; no vuelve a decidir `INTERVENE`, no elige IDs, no
modifica `reason_codes` y no llama herramientas.

El mensaje debe continuar sujeto al prompt y a la política de intervención
existentes. La ruta posterior (`SEND_INTERVENTION`, auditoría y `FINALIZE`)
conserva su contrato actual.

### Inbox y durabilidad

- `READ_INBOX` sigue siendo la única entrada de eventos al grafo.
- Los candidatos y decisiones pueden existir en `SupervisorState` sólo como
  datos transitorios del ciclo; no son una segunda fuente durable de eventos.
- Provenance debe conservar IDs que permitan volver al evento original del
  Inbox, sin copiar el Inbox a Memory MCP ni crear una cola paralela.
- El ACK sólo puede ejecutarse después de completar las llamadas de modelo,
  validación, persistencia MCP y, cuando corresponda, la persistencia durable
  de la intervención.
- Un fallo de DeepSeek, JEV, validación o MCP debe pasar por el `fail` existente
  y dejar el lote reintentable (`PENDING` o `FAILED` según la política actual).
- Un reintento del mismo `cycle_id` no debe crear memorias ni relaciones
  duplicadas ni reemplazar una materialización ya comprometida por una nueva
  respuesta del modelo.

## Functional Requirements

### FR-1. Extracción factual

1. El extractor SHALL ser una operación DeepSeek/OpenRouter separada de la
   materialización y SHALL identificarse observablemente como
   `EXTRACT_MEMORY_CANDIDATES`.
2. Cada candidato SHALL tener un `candidate_ref` único y secuencial, un claim
   factual no vacío, al menos una evidencia y provenance con al menos un
   `event_id` del lote reclamado.
3. El validador SHALL comprobar que cada `event_id` existe en
   `claimed_events`, que el candidato pertenece al proceso actual y que los
   textos respetan los límites configurados.
4. DeepSeek SHALL NOT decidir `keep`, categoría, estado, relaciones, routing o
   persistencia en esta operación.
5. La extracción SHALL tener `additionalProperties=false` o una validación
   equivalente para impedir que una respuesta no contratada pase a la etapa
   siguiente.

### FR-2. Curación JEV

6. El grafo SHALL introducir una etapa JEV explícita entre extracción y
   materialización.
7. La respuesta SHALL producir una decisión completa para cada candidato, aun
   cuando `keep=false`, y SHALL validar los rangos y enums definidos en este
   documento.
8. `category=SUMMARY` SHALL ser inválido en esta etapa. Los summaries siguen
   siendo responsabilidad del flujo de cierre de proceso existente.
9. Las relaciones SHALL usar sólo los tipos MCP allowlisted y referencias
   resolubles al proceso/proyecto actual.
10. La curación SHALL ser una operación sin efectos secundarios: JEV no puede
    llamar MCP, modificar Inbox, ACKear eventos ni llamar OpenCode.
11. No SHALL existir un umbral nuevo basado en `confidence`, `importance` o
    `progress_effect` para decidir `keep` o routing.

### FR-3. Materialización inmutable

12. `MATERIALIZE_MEMORIES` SHALL usar DeepSeek sólo para generar `title` y
    `content` de candidatos conservados.
13. La respuesta SHALL tener exactamente una materialización por candidato
    conservado y ninguna por candidato descartado.
14. Un output que cambie, repita o agregue metadata SHALL ser rechazado antes
    de cualquier llamada MCP.
15. La metadata efectiva usada para persistir SHALL ser byte/valor por valor la
    decisión JEV validada, salvo la traducción de nombres al contrato MCP
    especificada en FR-4.

### FR-4. Persistencia MCP

16. El persistidor SHALL depender del `MemoryClient` inyectado y no SHALL
    acceder directamente a SQLite ni construir JSON-RPC ad hoc.
17. SHALL usar sólo `memory_create`, `memory_link` y las lecturas MCP actuales,
    sin exigir una migración o herramienta nueva.
18. SHALL validar categorías, IDs, proyecto, tipos, estados, confidencias y
    relaciones antes de la primera mutación.
19. SHALL persistir provenance y los campos sin representación tipada mediante
    el envelope versionado en `description`, manteniendo el prefijo de
    idempotencia existente.
20. SHALL resolver referencias locales después de crear/reutilizar memorias y
    SHALL impedir relaciones self-edge, cross-project o hacia candidatos
    descartados.
21. SHALL reconocer una propuesta ya aplicada para el mismo ciclo y no SHALL
    crear duplicados en un reintento.
22. La respuesta de MCP SHALL ser validada: un ID ausente, categoría errónea,
    estado inesperado o metadata incompatible SHALL producir un error de
    contrato.

### FR-5. Decisión e intervención

23. El schema de `SupervisionDecision` SHALL incluir
    `evidence_memory_ids` y `reason_codes` como campos soportados.
24. Cuando `action=INTERVENE`, ambos campos SHALL ser listas no vacías y sus
    elementos SHALL pasar la validación de scope y allowlist.
25. Las demás acciones no SHALL requerir evidencia de intervención ni SHALL
    generar una llamada `BUILD_INTERVENTION`.
26. `BUILD_INTERVENTION` SHALL recibir explícitamente `decision`,
    `reason_codes`, `evidence_memory_ids`, `selected_memories` y `summary`.
27. `BUILD_INTERVENTION` SHALL fallar si falta cualquiera de los datos
    obligatorios para una decisión `INTERVENE`; no SHALL repetir la decisión
    con otro modelo.

### FR-6. Inbox y finalización

28. Los eventos SHALL permanecer en SQLite Inbox desde la recepción hasta el
    ACK/fail; ningún candidato o snapshot de LangGraph SHALL sustituirlos.
29. La finalización SHALL ACKear sólo un ciclo cuya curación y persistencia
    requeridas terminaron correctamente.
30. Cualquier error de contrato, proveedor o MCP SHALL impedir un ACK exitoso y
    SHALL conservar el error en la ruta operativa existente.
31. Las operaciones parciales antes de un error SHALL poder reconciliarse por
    `cycle_id`/`candidate_ref`; un reintento no SHALL duplicarlas ni reemplazar
    silenciosamente texto ya persistido.

## Non-Functional Requirements

- Las llamadas a DeepSeek, JEV y MCP SHALL conservar el comportamiento async y
  no bloquear el event loop.
- Los modelos no SHALL recibir herramientas ni credenciales, ni podrán ejecutar
  efectos secundarios.
- Los límites de candidatos, excerpts, titles, contents, relaciones y
  envelopes SHALL ser finitos, configurables donde ya exista configuración y
  validados antes de persistir.
- La serialización del envelope y de los payloads SHALL ser determinista para
  que los reintentos sean comparables.
- La observabilidad SHALL distinguir al menos extracción, curación,
  materialización, persistencia, decisión de intervención y delivery, con
  modelo/operación/latencia/IDs acotados y sin API keys ni payloads completos.
- La solución SHALL preservar la compatibilidad con memorias legacy que no
  tienen envelope de curación.
- Las operaciones MCP deben conservar las validaciones de proyecto, categoría,
  estados y relaciones ya impuestas por el servidor Go.

## Affected Components

La implementación probablemente afectará:

- `grams-app/supervisor/agent/schemas.py`: contratos y validadores de
  candidatos, curación, materialización, decisiones y reason codes.
- `grams-app/supervisor/agent/state.py` y `state_builder.py`: DTOs transitorios
  y payloads canónicos; no se debe convertir el estado en memoria durable.
- `grams-app/supervisor/agent/prompts.py`: prompts separados para extracción y
  materialización, sin mover routing a DeepSeek.
- `grams-app/supervisor/agent/nodes/`: nuevos límites/nodos o sustitución de
  `extract_memory_update.py`, además de `apply_memory_update.py`,
  `supervision_decision.py` y `build_intervention.py`.
- `grams-app/supervisor/agent/graph.py`: orden explícito de extracción,
  curación, materialización y aplicación.
- `grams-app/supervisor/agent/services/openrouter_service.py` y
  `jev_service.py`: sólo el wiring/operaciones necesarias; los clientes deben
  seguir siendo transportes, no dueños de política.
- `grams-app/supervisor/memory/client.py`: únicamente para usar o tipar la
  superficie MCP existente, sin acceso SQL ni nuevas herramientas.
- Tests Python bajo `grams-app/tests/`, incluyendo nuevos tests de curation,
  materialización, persistencia, intervención y Inbox.

No se espera modificar inicialmente `grams-app/memory-mcp/`, su esquema SQL ni
sus migraciones. Si el contrato de envelope requiere documentación de
compatibilidad, debe escribirse en esta propuesta o en documentación de
contrato, no mediante una migración.

## Constraints

- Memory MCP es la fuente durable de conocimiento semántico; Inbox es la fuente
  durable de eventos. LangGraph sólo coordina el procedimiento.
- Cada proceso mantiene exactamente `STRATEGY`, `EVIDENCE` y `SUMMARY`.
- El Action Agent/OpenCode no se modifica para administrar esta memoria.
- DeepSeek no puede elegir rutas, escribir MCP, ACKear Inbox ni enviar
  intervenciones.
- JEV no puede escribir MCP ni generar el texto final de memoria/intervención.
- Las decisiones de supervisión siguen siendo las actuales. En particular, el
  paso del tiempo no se convierte en una nueva razón directa para intervenir.
- El flujo debe seguir siendo seguro ante reintentos y conservar la clave de
  ciclo usada por la aplicación actual.
- No se puede solucionar la ausencia de campos MCP agregando SQL directo,
  cambiando el servidor Go o almacenando eventos completos en `description`.
  El envelope sólo debe contener metadata y referencias acotadas a eventos.

## Edge Cases

- Lote sin eventos finales utilizables: DeepSeek debe producir cero candidatos;
  no se realizan llamadas de curación/materialización innecesarias ni writes
  MCP.
- Candidato sin evidencia, con event ID de otro ciclo o con excerpt no válido:
  rechazo antes de JEV o, si la invalidez sólo puede detectarse allí, antes de
  persistir.
- JEV omite un candidato, lo repite, devuelve un enum no permitido o devuelve
  una relación a un candidato descartado: fallo de validación sin mutaciones.
- DeepSeek devuelve materialización para `keep=false`, omite una conservada,
  cambia su `candidate_ref` o añade metadata: fallo antes de MCP.
- Dos candidatos materializan el mismo contenido o dos referencias resuelven a
  una memoria existente: conservar las reglas de deduplicación actuales y
  evitar self-edges después de resolver IDs.
- Se pierde la respuesta después de `memory_create` o `memory_link`: el Inbox
  no se ACKea; el reintento debe consultar los prefijos/envelopes del ciclo
  antes de volver a mutar.
- Memoria legacy sin envelope: puede ser leída y relacionada, pero no debe ser
  actualizada sólo para añadir metadata de curación.
- Memoria existente con contenido equivalente y metadata incompatible: error de
  conflicto, no actualización silenciosa.
- `INTERVENE` sin `evidence_memory_ids`, con IDs fuera de scope, o sin
  `reason_codes`: rechazo de la decisión y no ejecución de `BUILD_INTERVENTION`.
- Un ID de evidencia existe en el proceso pero falta en el snapshot compacto:
  la selección debe cargarse mediante MCP de forma determinista y validarse
  antes de construir la intervención.
- No existe `SUMMARY`: `BUILD_INTERVENTION` recibe `summary=null`, no un texto
  inventado.
- Fallo de JEV/DeepSeek/MCP, timeout o respuesta truncada: no se ACKea el
  batch; se usa la política de retry/fail existente.

## Error Handling

1. Los errores de schema, scope, enum, rango, provenance y referencias deben
   detectarse antes de cualquier efecto secundario posterior.
2. Una respuesta inválida de DeepSeek o JEV debe producir un error clasificable
   por operación y dejar el lote sujeto a `fail_batch`/retry; no se debe
   degradar a memoria parcial o a una acción de supervisión inventada.
3. Un error MCP debe conservar el nombre de la operación y los IDs acotados,
   sin credenciales ni payloads completos. No se debe ocultar como ausencia de
   memoria.
4. Si hubo mutaciones MCP antes del error, la recuperación debe consultar el
   marcador de ciclo y el envelope antes de repetirlas. No se permite repetir
   ciegamente una mutación cuyo resultado sea desconocido.
5. La falta de `evidence_memory_ids` o `reason_codes` en `INTERVENE` es un
   error de contrato, no una autorización para llamar a DeepSeek sin contexto.
6. Un error de `BUILD_INTERVENTION` no debe enviar una intervención genérica ni
   cerrar el proceso; debe seguir el flujo de fallo del ciclo.
7. Sólo `FINALIZE` puede ACKear las claims y sólo después de que los pasos
   obligatorios hayan terminado. El estado `PROCESSED` nunca se debe inferir
   de que un modelo respondió.

## Acceptance Criteria by Phase

### Fase 1 — Contratos y extracción factual

- Los tests validan el schema de candidatos, límites y `candidate_ref` estable.
- Cada candidato aceptado contiene al menos un `event_id` presente en el lote
  y provenance verificable.
- Un fake DeepSeek demuestra que la extracción no genera `memory_create`,
  `memory_link`, ACK, routing ni categorías finales.
- La salida con evidencia faltante, event ID externo o campos extra falla antes
  de avanzar.
- La topología sigue cargando el contexto actual antes de extraer.

### Fase 2 — Curación JEV

- Un fake JEV produce y los tests verifican `keep`, `category`, `role`,
  `status`, `confidence`, `progress_effect`, `importance` y `relations` para
  cada candidato.
- Se rechazan enums/rangos inválidos, referencias desconocidas, relaciones a
  candidatos descartados, self-edges y scope cross-project.
- `keep=false` no llega a materialización ni a MCP.
- Las pruebas demuestran que no se añadió ningún umbral nuevo y que las
  opciones/umbrales de `SUPERVISION_DECISION` existentes permanecen iguales.

### Fase 3 — Materialización inmutable

- Un fake DeepSeek recibe sólo candidatos conservados y devuelve exactamente
  `candidate_ref`, `title` y `content`.
- El test compara la metadata antes y después de materializar y demuestra que
  no cambia ningún campo JEV.
- Una respuesta que intenta modificar categoría, estado, confianza,
  importance, progress effect o relaciones es rechazada sin llamadas MCP.
- Cero candidatos conservados produce cero materializaciones y cero writes.

### Fase 4 — Persistencia MCP sin migración

- Un fake `MemoryClient` registra únicamente operaciones MCP actuales y recibe
  `category_id`, `type`, `status`, `confidence`, title/content y source
  esperados.
- El test inspecciona el envelope de `description`, comprueba provenance,
  `progress_effect`, `importance`, metadata versionada y el prefijo de
  idempotencia.
- Las relaciones se crean mediante `memory_link`, con IDs resueltos y tipos
  allowlisted; no se usa SQL ni una herramienta nueva.
- Una repetición del mismo ciclo reutiliza las memorias/relaciones existentes,
  no crea duplicados y no reemplaza el texto original.
- Se conserva la compatibilidad de lectura para memorias legacy sin envelope.
- Las pruebas Go existentes de MCP y la migración inicial siguen pasando sin
  cambios de esquema.

### Fase 5 — Intervención condicionada por evidencia

- Un fake JEV que devuelve `INTERVENE` sólo es aceptado si incluye una lista no
  vacía y validada de `evidence_memory_ids` y `reason_codes`.
- Se rechazan IDs fuera del proceso/proyecto y reason codes no allowlisted.
- El test de `BUILD_INTERVENTION` inspecciona que el payload contiene la
  decisión, las razones, los IDs, exactamente las memorias seleccionadas y el
  summary (o `null`).
- El fake DeepSeek de intervención no puede cambiar la decisión ni las
  memorias; sólo devuelve el mensaje.
- `CONTINUE`, `NEED_MORE_MEMORY` y `CLOSE_PROCESS` no ejecutan
  `BUILD_INTERVENTION`.

### Fase 6 — Inbox y ciclo completo

- Una ejecución completa sigue el orden extracción → curación →
  materialización → persistencia → reload de contexto → supervisión.
- El ACK ocurre sólo después de terminar los writes MCP y la ruta de
  intervención requerida.
- Un fallo inyectado en cada llamada de DeepSeek, JEV, MCP o validación deja
  las claims en la ruta existente `PENDING`/`FAILED`, nunca `PROCESSED`.
- Un retry del mismo lote no duplica memorias, relaciones ni intervención y
  conserva la respuesta durable ya comprometida.
- Los tests existentes de Inbox, leases, ACK atómico, runtime, MCP client,
  memoria y observabilidad continúan pasando.

## Test Scenarios

1. **Candidato con provenance válida:** eventos finales `e1` y `e2`, candidato
   referenciando ambos, y comprobación de IDs, excerpts y cycle ID.
2. **Provenance inválida:** event ID ausente, de otro ciclo, duplicado o sin
   excerpt; comprobar que no se llama JEV/MCP después del fallo.
3. **Curación completa:** un candidato conservado y uno descartado, con todos
   los campos JEV y una relación entre el conservado y una memoria existente.
4. **Relaciones inválidas:** relación a descartado, tipo no allowlisted,
   self-edge, endpoint inexistente y endpoint de otro proyecto.
5. **Materialización estricta:** output correcto, output faltante, duplicado,
   extra y metadata modificada.
6. **Mapeo MCP:** inspeccionar argumentos exactos de `create` y `link`, incluido
   `type`, `status`, `confidence`, envelope y provenance.
7. **Deduplicación/replay:** ejecutar dos veces con el mismo cycle ID y
   verificar cero duplicados y estabilidad del primer texto materializado.
8. **Fallo después de write:** perder la respuesta de `create` o `link`,
   comprobar reconciliación por ciclo y ausencia de ACK falso.
9. **Decisión INTERVENE:** comprobar IDs de evidencia, códigos de razón,
   resolución de memorias seleccionadas y summary en el payload de
   `BUILD_INTERVENTION`.
10. **Intervención sin contexto:** quitar una memoria seleccionada, comprobar
    fallo cerrado y que no se envía un mensaje genérico.
11. **Rutas no interventivas:** ejecutar `CONTINUE`, `NEED_MORE_MEMORY` y
    `CLOSE_PROCESS`, comprobando que no se genera ni entrega intervención.
12. **Inbox durable:** reiniciar/reintentar entre extracción y finalización,
    comprobar que los eventos siguen en SQLite, que el lease se maneja por la
    ruta existente y que sólo un ciclo terminado recibe ACK.
13. **Regresión:** ejecutar la suite de Supervisor indicada por el repositorio,
    los tests del cliente MCP y `go test ./...`; verificar también
    `git diff --check` después de la implementación.

## Out of Scope

- Recalibrar JEV, cambiar `JEV_ACTION_MIN_CONFIDENCE`,
  `JEV_CONTEXT_SUFFICIENT_MIN_PROB`, `JEV_OUTCOME_MIN_CONFIDENCE` o el límite
  de expansión actual.
- Introducir una política que use `importance`, `progress_effect` o el número
  de memorias para intervenir automáticamente.
- Convertir el Inbox en memorias MCP, borrar eventos después de extraerlos o
  sustituir sus leases por estado LangGraph.
- Hacer que DeepSeek decida categorías, relaciones, acciones o delivery.
- Añadir migraciones, columnas tipadas para metadata, nuevas herramientas MCP,
  un repositorio durable de candidatos o una cola paralela.
- Rediseñar el formato de intervención, el cliente OpenCode o el mecanismo de
  entrega posterior a `BUILD_INTERVENTION`.
- Cambiar la ontología de procesos o crear categorías distintas de
  `STRATEGY`, `EVIDENCE` y `SUMMARY`.

## Assumptions and Ambiguities

- **Interpretación de `role`:** no existe un campo `role` en el MCP actual.
  Esta especificación interpreta inicialmente `role` como uno de los valores
  `MemoryType` ya soportados y lo mapea a `memory_create.type`. Si el producto
  necesita una taxonomía de roles distinta, debe confirmarse antes de
  implementar, porque no puede persistirse como campo tipado sin una migración.
- **Persistencia de campos sin columna:** se asume que `description` puede
  contener un envelope JSON versionado. Esto permite conservar
  `progress_effect`, `importance` y provenance sin cambiar el esquema. El
  envelope no ofrece todavía filtros SQL/MCP tipados; una consulta por esos
  campos requerirá una fase posterior.
- **Reason codes:** los códigos enumerados son una interpretación inicial de
  señales y motivos ya presentes en la arquitectura. Son explicaciones
  allowlisted, no nuevos detectores. Si se requiere otro vocabulario, debe
  definirse antes de congelar el schema.
- **Evidencia para INTERVENE:** esta propuesta exige al menos una memoria
  `EVIDENCE` y una razón porque el objetivo es que toda intervención quede
  grounded en memoria. Permitir intervenciones sin evidencia requeriría una
  política adicional y no se asume aquí.
- **Capacidad de respuesta de JEV:** se asume que el adaptador `JevClient`
  actual puede transportar las decisiones estructuradas requeridas mediante su
  contrato de preguntas. No se deben introducir herramientas ni generación
  libre para compensar una limitación del proveedor; si el proveedor no puede
  representar el contrato, la incompatibilidad debe reportarse antes de
  implementar.
- **Summary:** se asume que `LOAD_PROCESS_CONTEXT` puede proporcionar el
  summary actual o indicar su ausencia. La ausencia se representa como
  `null`, no se genera un summary adicional para `BUILD_INTERVENTION`.
