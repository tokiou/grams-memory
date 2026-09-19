# Observación: intervención observada en Raman

## Estado

Observación operacional separada; no es un requisito funcional de este cambio.

## Observación

En la ejecución denominada Raman, el Supervisor decidió intervenir únicamente
cuando se superó el umbral configurado de ausencia de eventos. La decisión no
se produjo antes del umbral ni por el mero paso del tiempo mientras existían
eventos/progreso observables.

La entrega de la intervención es una operación independiente de la decisión:
puede fallar cuando el proceso de OpenCode ya terminó o su endpoint dejó de
estar disponible. Por tanto, “decidió intervenir” no implica “intervención
entregada”. El fallo de entrega debe permanecer observable como error de
integración y no debe registrarse como una entrega exitosa.

## Alcance de esta observación

Este documento no fija un nuevo umbral, no cambia la política de intervención y
no prescribe recuperación adicional. Sirve para distinguir en tests y métricas
la decisión del Supervisor del resultado de su entrega a OpenCode.
