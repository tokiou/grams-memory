# Límite de contexto de Inbox para Jev y expansión progresiva

## Objective

Garantizar que cada solicitud enviada a la API de Jev respete el máximo de
contexto aceptado por dicha API, compactando de forma determinista la vista
del Inbox y de la memoria sin modificar los eventos durables. Además, el
Supervisor SHALL poder ejecutar hasta tres expansiones
`NEED_MORE_MEMORY` para recuperar memoria histórica paginada y, una vez
agotado ese límite, SHALL conservar la semántica de agotamiento que existe
actualmente.

## Relevant Context

- `READ_INBOX` reclama hasta `batch_size` eventos y actualmente conserva el
  payload completo en `SupervisorState` (`agent/nodes/read_inbox.py`).
- `build_jev_process_state` (`agent/state_builder.py`) transforma los eventos
  reclamados en eventos finales normalizados, pero incluye todos los eventos
  del ciclo y puede incluir listas de memorias, relaciones y grafos expandidos.
- `JevClient.system_one` envía `{"model", "state", "questions"}` a
  `/systemone`; actualmente no valida el tamaño serializado antes de llamar a
  la API.
- `LOAD_PROCESS_CONTEXT` carga hasta 50 elementos por categoría y marca la
  paginación. `EXPAND_GRAPH` recupera páginas anteriores y vecinos, acumula el
  resultado en `expanded_memory_context` y actualmente se cablea con
  `max_expansion_depth=2` en `graph.py` y `runtime.py`.
- La memoria durable sigue siendo el Inbox SQLite y Memory MCP. El contexto
  reducido sólo es una representación para Jev; no puede convertirse en una
  eliminación, ACK, alteración o reordenamiento durable de eventos.
- Las decisiones de Jev son dos llamadas en `SUPERVISION_DECISION` y una en
  continuidad. Las tres deben recibir el mismo contrato de presupuesto y la
  misma política de compactación.

## Scope

Incluye:

- Un presupuesto explícito para la solicitud serializada de Jev, incluyendo
  estado, preguntas y sobre de la solicitud.
- Compactación segura del contexto derivado del Inbox y de la memoria antes de
  cada llamada a Jev.
- Indicadores deterministas de truncamiento para que Jev conozca que la vista
  es parcial, sin enviar contenido descartado.
- Tres expansiones como máximo por ciclo y recuperación de páginas/memorias
  históricas dentro del alcance del proyecto.
- Pruebas unitarias, de integración del cliente y de topología del grafo.

No incluye cambios al contrato HTTP de `POST /events`, al esquema SQLite, a
los leases/ACKs, al servidor Go de Memory MCP, a los prompts de OpenRouter ni
a la política de decisiones de Jev salvo el metadato necesario para indicar
que el contexto está limitado.

## Expected Behavior

1. Antes de cada solicitud a Jev se construye una representación canónica y
   acotada. El tamaño se calcula sobre los mismos bytes JSON UTF-8 que se
   enviarían por HTTP, incluyendo `model`, `state` y `questions`.
2. La solicitud enviada nunca supera el límite configurado/documentado por la
   API. El cliente debe rechazar o compactar antes de realizar la llamada; no
   es válido confiar en un error HTTP de Jev como mecanismo de recorte.
3. El Inbox conserva el payload original y sus metadatos de lease aunque la
   vista enviada a Jev omita eventos o campos. Los demás nodos continúan
   usando la representación durable para ACK/fail y auditoría.
4. La compactación conserva primero la información de identidad de la tarea,
   proceso activo, estrategia/evidencia más recientes, errores y cambios
   operativos necesarios para decidir. Los eventos finales se mantienen en
   orden temporal/sequence y se descartan de forma determinista empezando por
   los menos recientes, dejando explícita la pérdida de historial.
5. Cada expansión `NEED_MORE_MEMORY` incrementa una profundidad de ciclo. Se
   permiten profundidades 1, 2 y 3; cada una puede recuperar páginas históricas
   no cargadas, memorias anteriores, relaciones, vecinos y resúmenes que estén
   dentro del proyecto y de las validaciones actuales.
6. Después de la tercera expansión no se ejecuta una cuarta llamada de
   expansión. Se conserva el comportamiento actual de
   `memory_expansion_exhausted`, de la ruta del grafo y del fallback de
   supervisión al alcanzar el límite.
7. Las expansiones acumuladas también pasan por la misma compactación antes de
   volver a Jev; la expansión no puede producir una solicitud que viole el
   presupuesto.

## Functional Requirements

### Presupuesto y cliente Jev

1. El sistema SHALL tener una única fuente de configuración para el máximo
   aceptado por Jev y SHALL validarlo como un valor finito y positivo.
2. El límite SHALL representar inequívocamente la unidad que exige la API
   real (bytes serializados, tokens o la unidad publicada por Jev). Si la API
   limita el cuerpo HTTP, el chequeo SHALL usar bytes UTF-8; si limita tokens,
   SHALL usar el tokenizador/estimador compatible y reservar espacio para el
   sobre de la solicitud.
3. `JevClient` SHALL medir el cuerpo final después de serializarlo con una
   codificación determinista y SHALL exponer en observabilidad el tamaño, la
   unidad y el límite, sin registrar API keys ni el payload completo.
4. Ninguna ruta (`ASSESS_PROCESS_CONTINUITY` ni las llamadas diagnóstica y de
   acción de `SUPERVISION_DECISION`) SHALL enviar un estado no acotado.
5. Si el estado mínimo, las preguntas y el sobre no caben incluso después de
   compactar, el cliente SHALL fallar antes de HTTP con un error explícito y
   clasificable como contexto imposible de representar. No SHALL truncar
   identificadores, opciones de respuesta, tipos de pregunta ni estructura
   JSON requerida para hacer que una solicitud inválida parezca válida.

### Compactación segura

6. La compactación SHALL ejecutarse sobre una copia/DTO de lectura y SHALL
   dejar intactos `claimed_events`, los payloads del Inbox, leases, IDs,
   secuencias y timestamps usados para finalización.
7. La representación compacta SHALL reutilizar la normalización existente de
   eventos finales y SHALL excluir campos no necesarios para Jev, payloads
   crudos, credenciales y datos de transporte que no formen parte del estado
   canónico.
8. Los límites por texto, evento, colección y estado total SHALL ser
   deterministas y verificables. Al recortar texto SHALL conservar el inicio
   y añadir una marca inequívoca de truncamiento; al recortar colecciones
   SHALL conservar los elementos de mayor prioridad y su orden estable.
9. El estado enviado SHALL incluir metadatos compactos como mínimo para saber
   que hubo truncamiento, qué secciones fueron truncadas y cuántos elementos
   fueron omitidos. Estos metadatos no SHALL incluir el contenido omitido.
10. El presupuesto SHALL aplicarse también a `expanded_memory`, relaciones y
    subgrafos. Si no cabe una expansión completa, se conservarán sus
    identificadores y los elementos más relevantes según una política estable,
    sin presentar la vista parcial como completa.
11. La compactación SHALL ser pura y repetible: igual estado y configuración
    producen igual representación y tamaño, independientemente de la hora
    actual o del orden incidental de un diccionario.

### Expansión de memoria

12. El valor por defecto del límite de expansión del grafo SHALL ser 3 tanto
    en `build_graph` como en `build_runtime` y sus pruebas SHALL verificar el
    wiring efectivo.
13. `EXPAND_GRAPH` SHALL aceptar una decisión `NEED_MORE_MEMORY` sólo cuando
    la profundidad actual sea menor que 3; profundidades negativas, no
    numéricas o mayores que 3 SHALL producir un error de validación o un
    estado agotado, nunca una cuarta expansión.
14. La recuperación SHALL mantener las validaciones actuales: IDs de memoria
    dentro del proceso, relaciones allowlisted, procesos relacionados dentro
    del proyecto y exclusión del proceso activo cuando corresponda.
15. Una expansión que no encuentre elementos nuevos SHALL seguir contando como
    la expansión consumida y SHALL permitir que la supervisión decida de nuevo
    con una vista marcada como parcial/agotada.
16. El límite de tres SHALL ser por ciclo de Supervisor y SHALL reiniciarse al
    iniciar un ciclo nuevo; no SHALL almacenarse como estado durable del Inbox.

## Non-Functional Requirements

- La comprobación de tamaño SHALL ocurrir antes de cada POST y no SHALL
  depender de una llamada de prueba a Jev.
- La compactación SHALL tener coste lineal respecto de los elementos que se
  reciben y no SHALL realizar llamadas adicionales a Inbox o Jev.
- Los logs y métricas SHALL permitir diagnosticar recortes y expansiones sin
  exponer payloads, textos completos, credenciales o datos sensibles.
- Cambiar el presupuesto SHALL ser una operación de configuración, no un
  cambio de código en cada nodo.

## Affected Components

- `grams-app/supervisor/agent/state_builder.py`: estado canónico, normalización
  y compactación del contexto.
- `grams-app/supervisor/agent/services/jev_service.py`: serialización,
  presupuesto, validación previa y observabilidad.
- `grams-app/supervisor/agent/state.py`: metadatos transitorios de presupuesto
  o truncamiento, si resultan necesarios.
- `grams-app/supervisor/agent/graph.py` y `agent/runtime.py`: profundidad
  máxima por defecto igual a 3.
- `agent/nodes/expand_graph.py` y `agent/nodes/supervision_decision.py`:
  límite, agotamiento y reentrada sin cambiar la política posterior al límite.
- Configuración del Supervisor y pruebas bajo `grams-app/tests/`.

## Constraints

- Sólo el Inbox es la fuente durable de eventos; LangGraph no SHALL mantener
  una cola alternativa ni mutar el evento para adaptarlo a Jev.
- Los modelos no reciben herramientas ni efectos secundarios. La recuperación
  sigue siendo determinista y controlada por Python/MCP.
- No se SHALL resolver el problema aumentando `batch_size`, eliminando el
  ACK/fail, enviando otro payload en una segunda petición automática o
  delegando el recorte al servidor Jev.
- Las distribuciones completas, confianzas, categorías, opciones y
  identificadores necesarios para validar las respuestas SHALL permanecer
  disponibles para el contrato de salida aunque el contexto de entrada sea
  compacto.

## Edge Cases

- Cero eventos, estado sin proceso, categorías vacías o expansión vacía.
- Un único evento o memoria cuyo texto individual supera el presupuesto total.
- Unicode multibyte, caracteres de escape, `null`, campos ausentes y objetos
  con tipos inesperados.
- Muchas memorias, relaciones, vecinos, procesos relacionados o tres
  expansiones que duplican los mismos IDs.
- El límite es menor, igual o mayor que el cuerpo ya compacto; el caso igual
  no SHALL producir un POST mayor por serialización distinta.
- La tercera expansión solicita otra expansión; debe marcar agotamiento y
  conservar el resultado posterior actual.
- El API rechaza un límite de configuración inválido o el contexto mínimo no
  cabe: ambos casos deben ser fallos previos y explícitos, no reintentos
  infinitos.

## Error Handling

- Un límite no válido SHALL impedir arrancar/crear el cliente con un mensaje
  que indique nombre, unidad y restricción.
- Un contexto imposible SHALL producir un error específico, registrable sin
  payload, y el ciclo SHALL seguir las reglas existentes de fail del Inbox;
  nunca debe ACKearse un ciclo cuyo procesamiento no terminó.
- Un fallo MCP durante expansión SHALL conservar la semántica actual de error,
  lease y retry; no SHALL marcar la expansión como exitosa.
- Un error HTTP de Jev SHALL seguir el manejo actual después del chequeo local;
  el tamaño registrado debe permitir distinguir límite local de rechazo remoto.

## Acceptance Criteria

1. Con un límite pequeño configurable, una solicitud que inicialmente excede
   el máximo se compacta y el cuerpo realmente enviado queda `<=` al máximo;
   el test inspecciona el cuerpo capturado, no sólo una métrica interna.
2. Todas las llamadas Jev del flujo usan el mismo guard y ninguna POST ocurre
   con un cuerpo sobredimensionado.
3. Tras compactar, el payload original, lease, IDs y orden durable del Inbox
   permanecen byte por byte sin modificación.
4. Los tests demuestran que se marcan secciones truncadas y que no se envía
   contenido omitido ni payload crudo.
5. Un contexto mínimo que no cabe falla antes de invocar el transporte y deja
   el evento sujeto al flujo de fail existente.
6. Un flujo con tres respuestas `NEED_MORE_MEMORY` ejecuta exactamente tres
   expansiones y puede volver a supervisión después de cada una.
7. Una cuarta solicitud `NEED_MORE_MEMORY` no ejecuta MCP adicional de
   expansión y produce exactamente el estado/ruta de agotamiento actual.
8. Las expansiones recuperan al menos una página histórica anterior cuando
   existe, preservan las validaciones de proyecto/categoría y no duplican
   elementos ya acumulados.
9. Las pruebas existentes de Inbox, leases, decisiones Jev, memoria y
   observabilidad continúan pasando.

## Test Scenarios

- Unitario: serialización determinista y cálculo del presupuesto en bytes
  UTF-8 (incluyendo Unicode), con límite exacto y límite excedido.
- Unitario: compactar muchos eventos conservando eventos recientes, errores,
  marcas de truncamiento y orden estable; verificar que la entrada no cambia.
- Unitario: compactar memorias, relaciones, subgrafos y páginas expandidas sin
  exceder el presupuesto ni perder sus IDs de referencia.
- Unitario: contexto mínimo imposible; verificar cero llamadas al HTTP client.
- Integración de `JevClient`: fake HTTP que captura cada JSON de continuidad,
  diagnóstico y acción y verifica el límite del cuerpo final.
- Regresión: una respuesta normal de Jev conserva distribuciones y confianza
  completas después de aplicar compactación.
- Topología: `build_graph`/`build_runtime` pasan profundidad 3 a
  `EXPAND_GRAPH`.
- Expansión: profundidad 0→1→2→3, recuperación de página anterior,
  deduplicación y rechazo de una cuarta expansión.
- Seguridad de alcance: IDs fuera del proceso, relaciones no allowlisted y
  procesos fuera del proyecto siguen fallando sin ACK prematuro.
- Ciclo completo: fallo de Jev o MCP después de un recorte deja el Inbox en
  el estado de retry/fail actual y no en `PROCESSED`.

## Out of Scope

- Cambiar el máximo del proveedor sin conocer/documentar el límite oficial.
- Resumir semánticamente con otro modelo, cambiar prompts o alterar la
  clasificación de Jev.
- Persistir el contexto compacto o el contador de expansiones como memoria
  durable.
- Cambiar los límites de `POST /events`, el esquema del Inbox o el servidor
  Memory MCP.

## Assumptions

- Se asume que “máximo aceptado por su API” puede expresarse como una unidad
  medible antes del transporte y estará disponible en documentación o
  configuración de despliegue.
- No se encontró en el repositorio el valor ni la unidad oficial del límite de
  Jev. Esto es una ambigüedad significativa: antes de implementar debe
  confirmarse si el proveedor limita bytes del cuerpo, tokens por contexto,
  tokens por pregunta o una combinación. La implementación no debe fijar un
  número inventado; debe usar ese dato confirmado y reservar el overhead
  correspondiente.
- Se asume que “conservar el comportamiento actual” después de tres
  expansiones significa mantener la señal `memory_expansion_exhausted`, la
  ruta actual del grafo y el fallback de supervisión ya existente, no crear
  nuevas decisiones de negocio.
