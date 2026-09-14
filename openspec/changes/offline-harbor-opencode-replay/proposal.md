# Replay offline de Harbor + OpenCode para el Supervisor

## Objective

Crear un arnés de simulación y replay que reproduzca, sin iniciar Harbor ni
OpenCode en tiempo real, una secuencia guardada de eventos normales de OpenCode
y ejecute el Supervisor existente usando una llamada real al modelo Qwen para
decidir acciones. Cada ejecución debe producir una traza auditable de eventos,
llamadas, payloads (con secretos redactados), decisiones, respuestas, errores,
tiempos y frecuencia, de modo que una prueba controlada pueda inspeccionarse y
compararse sin efectos externos no autorizados.

## Relevant Context

- El receptor público actual es `POST /events`, con persistencia durable en el
  Inbox SQLite y procesamiento asíncrono por un runtime single-flight.
- El árbol vigente del Supervisor está bajo
  `grams-app/supervisor/` y separa `inbox/`, `runtime/`, `agent/`,
  `memory/` y `opencode/`.
- El grafo vigente contiene los nodos `REVIEW`, `READ_INBOX`,
  `MEMORY_OPERATION` e `INTERVENE`; `REVIEW` usa un modelo OpenAI-compatible y
  puede producir `DONE`, `INTERVENE` u operaciones de memoria.
- El plugin normaliza eventos con `schema_version`, `type`, `source_event`,
  `timestamp`, `session_id` y `payload`, y emite eventos finales como
  `TEXT_FINAL`, `REASONING_FINAL`, `TOOL_CALL_FINAL`, `TOOL_RESULT_FINAL`,
  `FILE_CHANGE_FINAL` y `MESSAGE_COMPLETED`.
- La configuración existente del modelo usa `MODEL_DEPLOYMENT`,
  `MODEL_API_KEY` y `MODEL_BASE_URL`; el script
  `scripts/qwen_review_latency.py` demuestra el formato de respuesta
  `choices[0].message.content` y la necesidad de extraer una decisión JSON.
- El flujo Harbor actual instala OpenCode y publica su API en el trial; ese
  camino debe permanecer separado del replay. La documentación existente
  indica que todavía no existe almacenamiento persistente de trazas.

## Scope

Incluye:

1. Un formato versionado para fixtures de replay y su validación.
2. Un runner invocable localmente que lea fixtures, reproduzca sus eventos en
   el orden indicado y ejecute el Supervisor sin procesos Harbor/OpenCode.
3. Un cliente de modelo Qwen real, configurado por las variables existentes,
   como única dependencia externa obligatoria del replay.
4. Sustitutos controlados para las interfaces de OpenCode y de operaciones de
   memoria que registren las llamadas y devuelvan respuestas configurables,
   sin ejecutar acciones reales.
5. Una traza de auditoría por ejecución, legible por máquina y acompañada por
   un resumen de conteos, decisiones, errores, latencias y frecuencia.
6. Modos de tiempo documentados para preservar timestamps del fixture o
   acelerar la reproducción sin esperar los intervalos originales.
7. Pruebas automatizadas del parser, runner, aislamiento y auditoría, además
   de una prueba de integración opcional contra un endpoint Qwen configurado.

No incluye cambios al contrato de `POST /events`, al plugin, a Harbor, a
OpenCode, al protocolo MCP ni a la política de decisión del Supervisor salvo
los puntos de inyección necesarios para ejecutar el replay.

## Expected Behavior

1. El runner valida el manifest y todos los eventos antes de comenzar. Si el
   fixture no es válido, no inicia el Supervisor ni hace una llamada al modelo.
2. El runner crea un espacio de trabajo y una base Inbox/checkpoint aislados
   para la ejecución. Inyecta cada evento como lo haría el receptor durable y
   conserva orden, identidad, timestamp y payload del fixture.
3. Durante el replay no se inicia un proceso, contenedor o servidor Harbor ni
   un proceso, cliente o API de OpenCode. Las llamadas que el Supervisor haría
   a OpenCode se atienden mediante el sustituto controlado.
4. Cuando el grafo llega a `REVIEW`, el runner llama al endpoint real Qwen con
   el contexto que recibiría el Supervisor. La respuesta se valida como una
   decisión compatible con el contrato vigente; no se sustituye por una
   decisión fija ni por un modelo falso en el modo real.
5. Las operaciones de memoria y las intervenciones se ejecutan sólo contra
   sustitutos configurados. Una operación seleccionada puede devolver éxito,
   error o respuesta programada, pero no puede mutar OpenCode, Harbor, el
   repositorio de trabajo ni una memoria externa.
6. La ejecución continúa hasta consumir los eventos y el trabajo generado, o
   termina con un estado terminal explícito (`completed`, `failed`, `aborted` o
   `validation_failed`). Un fallo no se oculta como una decisión válida.
7. La traza permite reconstruir la relación entre evento de entrada, ejecución
   del grafo, llamada externa, respuesta, decisión, operación simulada y
   resultado final.

## Functional Requirements

### Fixture y reproducción

1. El fixture SHALL tener un manifest con, como mínimo, `schema_version`, un
   `fixture_id`, una descripción, el origen declarado, la versión del formato,
   la lista ordenada de eventos y el modo de tiempo esperado.
2. Cada evento SHALL conservar como mínimo `event_id` estable del fixture,
   `timestamp` original si existe, `type`, `session_id` nullable y el payload
   JSON completo. El formato SHALL admitir cualquier valor JSON dentro del
   payload, incluidos `null`, arrays y escalares.
3. El validator SHALL rechazar IDs duplicados, eventos no JSON, timestamps
   imposibles cuando el modo elegido requiera timestamps, tipos ausentes y
   referencias a respuestas simuladas inexistentes.
4. El runner SHALL ofrecer al menos los modos `preserve_timing` y
   `as_fast_as_possible`. En el primero respetará el delta entre timestamps
   dentro de un límite configurable; en el segundo no esperará esos deltas,
   pero conservará los timestamps originales en la traza.
5. El runner SHALL preservar el orden del fixture. La concurrencia entre
   eventos sólo podrá introducirse si el fixture la declara explícitamente;
   el modo por defecto será secuencial y reproducible.
6. Una misma ejecución SHALL usar un `replay_run_id` único y no SHALL mezclar
   Inbox, checkpoints, trazas o archivos de distintas ejecuciones.
7. El runner SHALL permitir seleccionar una ventana o subconjunto de eventos
   sin alterar los IDs originales y SHALL registrar esa selección en el
   manifest de salida.

### Aislamiento de Harbor, OpenCode y efectos

8. El modo replay SHALL fallar si intenta iniciar Harbor/OpenCode o si una
   dependencia de runtime intenta abrir una conexión a la API real de
   OpenCode.
9. El sustituto de OpenCode SHALL cubrir las operaciones que el grafo use en
   la versión vigente, incluyendo contexto, `prompt_async` y `abort`, y SHALL
   responder con datos o errores definidos por el fixture/configuración.
10. El sustituto de memoria SHALL registrar nombre de operación, argumentos y
    resultado, y SHALL impedir por defecto llamadas al MCP Go real o a otra
    memoria persistente externa.
11. El replay SHALL poder ejecutar `DONE`, `INTERVENE` y cada operación de
    memoria que el Supervisor soporte. Una respuesta simulada de error,
    timeout o payload inválido SHALL seguir las mismas rutas de error/retry
    del Supervisor real.
12. Ningún sustituto SHALL marcar como exitosa una llamada que no tenga una
    respuesta configurada o que haya sido configurada como error.

### Qwen real y decisiones

13. El modo que se denomine `real_qwen` SHALL requerir valores no vacíos para
    `MODEL_DEPLOYMENT`, `MODEL_API_KEY` y `MODEL_BASE_URL`, y SHALL usar esos
    valores sin sustituirlos por un modelo local o una respuesta hardcodeada.
14. Cada solicitud al modelo SHALL registrar modelo/deployment, endpoint sin
    credenciales, temperatura/parámetros, identificadores de replay y evento,
    timestamp de inicio/fin, duración, estado HTTP, conteo de tokens si el
    proveedor lo devuelve y payload enviado redactado.
15. Cada respuesta Qwen SHALL registrar payload recibido redactado, estado,
    duración y el texto/JSON que se intentó parsear. La API key y headers de
    autorización SHALL quedar fuera de la traza.
16. La decisión normalizada SHALL registrar acción, razón si existe, campos
    de operación/intervención, evento que la originó y la respuesta exacta
    usada para derivarla, con redacción aplicada.
17. Una decisión que no sea JSON válido, no tenga acción permitida o tenga
    campos incompatibles SHALL ser un error observable y reintentable según la
    política vigente; no SHALL convertirse silenciosamente en `DONE`.
18. El runner SHALL permitir configurar timeout, número de reintentos y
    temperatura, registrando la configuración efectiva sin secretos.

### Auditoría, conteos y frecuencia

19. La salida SHALL contener un registro estructurado por línea o equivalente
    consultable, con `replay_run_id`, secuencia monótona, timestamp UTC y tipo
    de registro.
20. SHALL existir registros para, como mínimo: inicio/fin, validación,
    inyección de cada evento, claim/ack/retry/fallo, inicio/fin de cada nodo,
    cada llamada a Qwen, cada llamada simulada de OpenCode/MCP, cada decisión,
    cada error y el estado final.
21. Cada llamada SHALL incluir servicio, operación, dirección (entrada/salida),
    duración, outcome y correlación con `event_id`, `run_id` y
    `replay_run_id` cuando estén disponibles.
22. La auditoría SHALL incluir conteos por evento, tipo, servicio, operación,
    acción decidida, outcome y retry; también SHALL incluir frecuencia como
    timestamps de ocurrencia, intervalo desde la ocurrencia anterior y tasa o
    conteo por ventana temporal para eventos y llamadas.
23. La auditoría SHALL conservar el payload de entrada, solicitudes y
    respuestas necesarias para reproducir/inspeccionar el caso, aplicando una
    política explícita de redacción a API keys, tokens, cookies, headers de
    autorización y secretos. La documentación SHALL identificar los campos
    redactados y, cuando se omita un valor, conservar tipo, tamaño y hash para
    comparación.
24. La traza SHALL ser cerrada y suficientemente consistente para que un
    análisis posterior pueda detectar una llamada sin respuesta, una decisión
    sin llamada de modelo o una acción sin registro de decisión.
25. El runner SHALL producir un resumen separado que indique cantidad de
    eventos reproducidos, llamadas Qwen, llamadas simuladas, decisiones por
    acción, errores, retries, duración total, latencia min/mediana/promedio/max
    y frecuencia observada.

### CLI y operación controlada

26. SHALL existir un comando documentado con, como mínimo, ruta de fixture,
    modo de tiempo, directorio de salida y selección del perfil de sustitutos.
    El comando SHALL imprimir la ruta de la traza y el estado final.
27. El runner SHALL soportar un modo de validación que no llame a Qwen ni a
    sustitutos, y un modo de ejecución `real_qwen` que sí llame al endpoint
    configurado.
28. El directorio de salida SHALL contener el manifest efectivo, la traza, el
    resumen y una indicación de versiones/configuración no secreta usada.
29. La ejecución SHALL ser abortable. Al abortar, debe cerrar la llamada en
    curso según el timeout configurado, escribir el estado `aborted` y no
    confirmar como procesados los eventos que no terminaron correctamente.

## Non-Functional Requirements

- El replay SHALL ser reproducible respecto de fixture, versión de formato,
  configuración de sustitutos y orden; la decisión de Qwen puede variar y la
  traza debe dejar visible el modelo y parámetros usados.
- La auditoría no SHALL bloquear indefinidamente el procesamiento y debe
  escribirse de forma atómica o cerrarse con un estado de recuperación visible.
- Los secretos nunca SHALL aparecer en logs, nombres de archivo, excepciones o
  resumen. Payloads potencialmente sensibles sólo se conservarán mediante la
  política de redacción definida.
- El modo replay no SHALL requerir Docker, Harbor, OpenCode ni el MCP Go para
  las pruebas unitarias y de validación. Sólo la prueba `real_qwen` requiere
  conectividad al endpoint Qwen.
- El runner SHALL tener límites configurables para tamaño de fixture, tamaño
  de payload, número de eventos, timeout de llamada y tamaño de traza.

## Affected Components

- Nuevo paquete/comando bajo un área de replay del repositorio, sin mezclarlo
  con el entrypoint de producción del Supervisor.
- Interfaces de inyección del runtime/grafo para seleccionar cliente Qwen y
  sustitutos de OpenCode/memoria.
- Reutilización de `supervisor/inbox`, `supervisor/runtime`,
  `supervisor/agent` y `supervisor/observability` donde sea posible.
- Fixtures y pruebas bajo el área de tests correspondiente.
- Documentación de ejecución local y variables de entorno. No se deben editar
  los contratos actuales del plugin para implementar el replay.

## Constraints

- No ejecutar Harbor, `opencode serve`, `opencode run`, `--attach` ni conectar
  con `OPENCODE_BASE_URL` durante un replay.
- Qwen real es obligatorio para `real_qwen`; un fake sólo podrá usarse en
  validación y tests, nunca ocultamente en ese modo.
- No cambiar `POST /events`, el formato normalizado del plugin, los estados
  del Inbox ni la semántica de ack/retry del Supervisor.
- El replay no debe usar una memoria externa ni producir intervenciones reales.
- Los dobles deben implementar las interfaces existentes, no una segunda
  lógica de decisión que diverja del grafo.
- Las trazas son artefactos de prueba: no deben convertirse en la fuente
  durable de eventos del Supervisor ni sustituir SQLite/checkpoints.

## Edge Cases

- Fixture vacío, truncado, con JSON inválido, timestamp ausente, timestamps
  fuera de orden, timestamps idénticos o timestamp futuro.
- Eventos duplicados con payload idéntico pero `event_id` distinto, y eventos
  repetidos con el mismo `event_id`.
- Sesiones ausentes, payload `null`, arrays, escalares y payloads muy grandes.
- Qwen devuelve JSON envuelto en texto, JSON no objeto, acción desconocida,
  respuesta vacía, HTTP 4xx/5xx, timeout, desconexión o rate limit.
- El modelo devuelve una acción que requiere campos ausentes, o una llamada
  simulada responde error después de una decisión válida.
- Reintento de un evento, lease expirado, excepción del grafo y cancelación
  durante una llamada Qwen o un sustituto.
- Dos eventos producen decisiones consecutivas para la misma sesión; la traza
  debe distinguir sus `event_id` y no fusionar sus frecuencias.
- El endpoint Qwen es alcanzable pero no está configurado; debe fallar antes
  de comenzar la reproducción que requiere modelo.

## Error Handling

- Errores de fixture/configuración SHALL terminar como `validation_failed`, con
  diagnóstico y cero llamadas externas.
- Errores Qwen, parseo de decisión y errores de sustitutos SHALL conservar
  tipo, estado, duración y mensaje sanitizado; se aplicará retry/fallo según
  las reglas existentes y el resultado no SHALL ser un ack falso.
- Un error de auditoría SHALL ser visible y SHALL marcar la ejecución como
  `failed` si impide garantizar el registro requerido; no se continuará
  silenciosamente con una traza incompleta.
- Cancelación o timeout de ejecución SHALL cerrar recursos, conservar el último
  evento y escribir el estado terminal disponible.
- Las excepciones SHALL excluir API keys, authorization headers, cookies y
  payloads no redactados.

## Acceptance Criteria

1. Con un fixture de eventos finales normalizados, el comando de replay valida,
   crea un Inbox aislado y reproduce todos los eventos sin iniciar ni contactar
   Harbor/OpenCode.
2. En `real_qwen`, una ejecución usa las tres variables de modelo existentes,
   realiza llamadas al endpoint Qwen y registra request/response, decisión,
   duración y correlación sin exponer la API key.
3. Un servidor de prueba que detecte conexiones a OpenCode/Harbor confirma que
   el replay no realiza ninguna; los sustitutos reciben las operaciones
   esperadas y no producen efectos externos.
4. Las decisiones `DONE`, `INTERVENE` y de memoria recorren las rutas del
   grafo vigentes; una decisión inválida o un error simulado no produce ack
   falso y queda en la traza.
5. La salida contiene manifest efectivo, traza ordenada y resumen con conteos,
   latencias y frecuencia por evento/llamada/acción, y permite detectar
   llamadas o decisiones sin pareja.
6. `as_fast_as_possible` no espera los intervalos del fixture y
   `preserve_timing` los respeta dentro de la tolerancia documentada, sin
   cambiar los timestamps originales registrados.
7. Repetir el mismo fixture con respuestas Qwen y sustitutos grabados produce
   el mismo orden, conteos, acciones, estados de Inbox y resumen; cualquier
   diferencia de Qwen live queda identificada por la traza.
8. Fixtures inválidos, credenciales ausentes, timeouts, respuestas malformadas,
   cancelación y fallos de auditoría terminan con estados explícitos y sin
   dejar procesos, conexiones o archivos temporales no reclamados.

## Test Scenarios

1. Validar fixtures con objeto, array, string, número, booleano y `null`, y
   rechazar duplicados, tipos ausentes y JSON corrupto.
2. Reproducir una secuencia normal del plugin (`TEXT_FINAL`, `TOOL_CALL_FINAL`,
   `TOOL_RESULT_FINAL`, `FILE_CHANGE_FINAL`, `MESSAGE_COMPLETED`) y comprobar
   orden, payload y correlación.
3. Ejecutar cada acción soportada con Qwen stub sólo en tests, verificando que
   las rutas reales del grafo llaman al sustituto correcto.
4. Ejecutar `real_qwen` contra un endpoint OpenAI-compatible de prueba y
   comprobar deployment, payload redactado, parseo de decisión, timeout,
   error HTTP y respuesta inválida.
5. Configurar el sustituto OpenCode para `context`, `prompt_async` y `abort`,
   y comprobar argumentos, respuesta, errores y ausencia de red real.
6. Configurar respuestas de memoria exitosas, erróneas y ausentes; verificar
   que no se llama MCP real y que retry/FAILED siguen la semántica vigente.
7. Comprobar la traza completa de un evento: inyección, claim, nodos, Qwen,
   decisión, operación, ack y resultado; después comprobar conteos e
   intervalos/frecuencia.
8. Comparar ambos modos de tiempo con timestamps separados y timestamps
   iguales, incluyendo un evento fuera de orden y uno sin timestamp.
9. Interrumpir durante Qwen y durante una operación simulada; comprobar estado
   `aborted`, cierre de recursos y ausencia de ack falso.
10. Buscar API keys, tokens, cookies y headers de autorización en todos los
    artefactos de salida y comprobar que no aparecen; comprobar hashes/metadatos
    para valores redactados.
11. Ejecutar dos replays concurrentes con el mismo fixture y verificar
    directorios, `replay_run_id`, bases, trazas y contadores independientes.
12. Ejecutar el modo de validación y verificar que no realiza ninguna llamada
    Qwen ni simulada y que no crea un resultado de ejecución parcial.

## Out of Scope

- Ejecutar Harbor u OpenCode como parte del replay.
- Capturar nuevas trayectorias desde una ejecución live; el fixture debe
  existir antes del replay.
- Evaluar la calidad semántica de Qwen, entrenarlo, cambiar su prompt de
  producción o comparar modelos.
- Garantizar determinismo de decisiones obtenidas de Qwen live.
- Dashboard, almacenamiento remoto de trazas, métricas Prometheus,
  escalamiento multi-proceso o un broker nuevo.
- Reproducir efectos reales de filesystem, git, MCP, OpenCode o Harbor.

## Assumptions

- “Offline” significa sin Harbor/OpenCode ni sus APIs en tiempo real; el
  endpoint Qwen real sí debe ser alcanzable en el modo `real_qwen`. Si también
  se exige ausencia total de red, no es compatible con “Qwen real” y debe
  definirse una modalidad separada con respuestas grabadas.
- Los eventos de entrada siguen el envelope normalizado por el plugin y el
  Supervisor puede procesarlos mediante sus interfaces actuales.
- Se permite inyectar clientes/adapters en composición de tests o replay sin
  cambiar el contrato de producción.
- Las operaciones simuladas de memoria son suficientes para probar decisiones
  y control de flujo; validar la integración real con MCP Go requiere las
  pruebas de integración ya descritas por `supervisor-v2.md`.

## Ambiguities Requiring Confirmation

- Debe confirmarse si el artefacto de auditoría puede conservar payloads de
  negocio completos tras redacción o si existen campos adicionales que deban
  excluirse por política de privacidad. Esta decisión afecta el esquema de la
  traza, no el flujo del replay.
- Debe confirmarse qué respuesta simulada de contexto OpenCode corresponde a
  cada `session_id` y si se usará un catálogo por evento o por sesión. La
  especificación exige que sea explícito y versionado, pero no fija el
  contenido semántico.
