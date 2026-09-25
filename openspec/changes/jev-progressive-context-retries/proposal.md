# Reintentos de compactación progresiva para Jev

## Objective

Evitar que una llamada a Jev falle innecesariamente cuando el cuerpo serializado excede el presupuesto: reducir progresivamente el estado y, cuando sea necesario, dividir el payload de preguntas en lotes íntegros, y volver a validar antes de cada intento HTTP. Se permiten hasta tres reintentos adicionales tras el intento inicial ante rechazos remotos por tamaño, sin repetir un payload idéntico.

## Relevant Context

- `grams-app/supervisor/agent/services/jev_service.py` calcula el overhead de `model` y `questions` antes de compactar el estado con `compact_jev_state`.
- Si las preguntas por sí solas consumen el presupuesto, actualmente se lanza `ValueError` antes de llamar a Jev; compactar estado no resuelve ese caso.
- Si el estado compactado más el resto del cuerpo todavía excede el límite, actualmente se produce un error local en lugar de una secuencia de compactación/reintento.
- El límite se comprueba sobre la solicitud serializada en bytes UTF-8.

## Scope

Incluye la política de compactación progresiva y reintentos en el cliente Jev, incluyendo casos de presupuesto excedido por estado o por preguntas. No incluye reintentos por errores HTTP que no indiquen límites de tamaño, errores de transporte, cambios al contrato de respuesta de Jev ni cambios a eventos durables.

## Expected Behavior

El cliente prepara un cuerpo dentro del presupuesto antes de enviarlo. Si el cuerpo inicial no cabe, reduce progresivamente el estado y vuelve a medir. Si el overhead de preguntas impide que exista presupuesto positivo para estado, divide el conjunto de preguntas en lotes más pequeños sin descartar ni cambiar preguntas, y combina las respuestas por clave. Ante un rechazo remoto explícito por tamaño, reduce progresivamente el estado y reintenta hasta tres veces adicionales; nunca repite el mismo payload.

## Functional Requirements

1. `JevClient.system_one` SHALL medir el cuerpo JSON UTF-8 final, incluyendo `model`, `state` y `questions`, antes de cada POST.
2. Si el cuerpo excede el límite por el tamaño del estado, el cliente SHALL aplicar compactación progresiva del estado entre intentos, sin mutar el estado recibido. Cada intento de compactación SHALL reducir el tamaño enviado o terminar con un error local clasificado; no SHALL reintentar un cuerpo idéntico.
3. Si `model` y `questions` sin estado exceden el límite, el cliente SHALL dividir las preguntas en lotes que quepan. SHALL conservar íntegramente cada definición, nombre y tipo de pregunta, y SHALL combinar las respuestas sin colisiones. Tras dividir, SHALL fallar explícitamente si faltan respuestas o hay claves inesperadas; no SHALL descartar silenciosamente preguntas ni respuestas.
4. Si una pregunta individual o el estado mínimo no caben ni tras dividir, SHALL fallar antes de HTTP con un error local explícito.
5. Ante un HTTP 413 o un HTTP 400 que identifique explícitamente un límite de contexto/tokens/tamaño, la secuencia SHALL realizar un intento inicial y como máximo tres reintentos adicionales con compactación progresiva del estado. Otros errores HTTP y de transporte no SHALL activar reintentos.
6. Cada POST SHALL contener exactamente el payload medido y validado para ese intento. Ninguna llamada SHALL superar el presupuesto local configurado.
7. Los errores locales finales SHALL distinguir si no cabe una pregunta individual o si no cabe el estado mínimo más el overhead; los logs SHALL evitar registrar payloads o cuerpos de error completos, así como credenciales.

## Non-Functional Requirements

- La política SHALL ser determinista: misma entrada y configuración producen la misma secuencia de payloads.
- Los reintentos SHALL estar acotados; no se harán POST de prueba fuera del presupuesto.

## Affected Components

- `grams-app/supervisor/agent/services/jev_service.py`: presupuesto, preparación de payload, reintentos y errores.
- `grams-app/supervisor/agent/state_builder.py`: reutilización o ajuste de compactación progresiva, si hace falta.
- Pruebas de cliente Jev y compactación bajo `grams-app/tests/`.

## Constraints

- Respetar la estructura y tipos de pregunta que exige Jev y la validación posterior de respuestas.
- No alterar la semántica durable del Inbox ni reintentar fallos HTTP no relacionados con límites de tamaño.
- El estado compactado es una vista de solicitud; la entrada original permanece intacta.

## Edge Cases

- Preguntas solas justo dentro del límite, exactamente en el límite y excediéndolo.
- Estado vacío, estado mínimo mayor que el espacio restante y estado grande que cabe tras más de una reducción.
- La reducción de estado o preguntas alcanza una representación mínima sin poder reducir más.
- Unicode y escapes JSON que cambian el tamaño UTF-8 respecto del número de caracteres.
- Un paso de compactación que no cambia sustancialmente el estado no debe consumir un reintento con solicitud duplicada.

## Error Handling

Los casos imposibles SHALL fallar localmente antes de HTTP con errores diferenciables para preguntas individuales y estado irreducible. Tras agotar los tres reintentos adicionales por error remoto de tamaño, SHALL reportarse el agotamiento sin repetir payloads. Los errores HTTP no relacionados con tamaño conservarán su manejo actual y no activarán compactación/reintentos.

## Acceptance Criteria

1. Una solicitud cuyo estado excede el límite inicialmente puede caber luego de compactaciones sucesivas; el transporte recibe sólo el cuerpo que cabe.
2. Una solicitud con preguntas que exceden el presupuesto sin estado se divide en lotes, conserva todas las claves y combina las respuestas; una pregunta individual imposible falla localmente.
3. Ningún test observa un POST cuyo cuerpo UTF-8 exceda el máximo configurado.
4. La secuencia ejecuta a lo sumo cuatro preparaciones (inicial más tres reintentos) y no envía payloads duplicados.
5. El fallo por pregunta individual imposible y el fallo por estado mínimo se distinguen y ocurren sin invocar el cliente HTTP.
6. Se prueba que la entrada original no se modifica, que tipos/opciones sobreviven intactos a la división, que se combinan respuestas/uso y metadatos (el último lote prevalece ante conflictos), y que respuestas incompletas se rechazan.

## Test Scenarios

- Cliente fake que captura bytes: límite exacto, sobre límite por estado y compactación progresiva que termina bajo el máximo.
- Preguntas por sí solas sobre el máximo; verificar lotes completos y combinación de respuestas, y cero POST/error clasificable si una pregunta individual es imposible.
- Estado irreducible; verificar cero POST y error de estado/contexto.
- Forzar rechazos remotos por tamaño: exactamente tres reintentos adicionales como máximo.
- Compactación sin cambio de tamaño: terminar sin POST duplicado ni bucle.
- Respuesta válida tras reintento: verificar que distribución y validación de respuesta conservan el contrato actual.
- HTTP 413/contexto: verificar reintentos con compactación progresiva hasta éxito o límite.
- HTTP 400 no relacionado con tamaño: verificar que no se activa la política de reintentos.

## Out of Scope

- Reintentar errores HTTP no relacionados con tamaño, timeouts o fallos de red.
- Cambiar el límite o su unidad, prompts, preguntas de negocio o contrato de salida de Jev.
- Usar otro modelo para resumir estado/preguntas.

## Assumptions

- “Triple retry” significa hasta tres reintentos adicionales después del intento inicial (máximo cuatro preparaciones/POST posibles); no implica repetir el mismo payload.
- Las preguntas son evaluaciones identificadas por clave, por lo que dividirlas en lotes y combinar sus respuestas conserva el contrato de salida.
