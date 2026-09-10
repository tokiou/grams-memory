# Delta spec: REVIEW, progreso y `SUPERVISOR_TICK`

Este delta complementa `openspec/supervisor-v2.md` y la deuda
`openspec/changes/supervisor-long-running-work-observability/proposal.md`.

## SHALL

- Mantener `supervisor.app:app`, `POST /events`, `SupervisorRuntime`,
  `build_graph` y `make_review_node` como puntos de integración compatibles.
- Ejecutar la lectura `MemoryClient.search` acotada al proyecto de
  `root_session_id` antes de `ReviewModel.decide` en toda sesión no `default`,
  incluyendo ticks.
- Exponer timestamps y métricas de progreso en `SupervisorState`, contexto,
  `assessment` y logs, con las unidades y fuentes descritas en la propuesta.
- Generar ticks sólo para raíces activas sin trabajo pendiente, sin crear filas
  sintéticas en Inbox y sin romper single-flight.
- Hacer configurable y observable la intervención por ausencia de progreso.
- Distinguir de forma exacta las fuentes `deterministic`, `model` y `fallback`.

## SHOULD

- Mantener las funciones extraídas de REVIEW libres de I/O de runtime salvo la
  unidad explícitamente responsable de cada cliente, para que puedan probarse
  con dobles.
- Reutilizar el mismo pipeline de contexto/decisión para eventos normales y
  ticks, evitando una política paralela.

## Verification

La implementación se considera conforme cuando los escenarios 1–10 de la
propuesta pasan y los tests verifican tanto el orden MCP→modelo como la ausencia
de inserción de un evento `SUPERVISOR_TICK` en SQLite.
