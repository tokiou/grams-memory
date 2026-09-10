# Deuda: observabilidad de trabajo largo y progreso

## Contexto

La politica actual del Supervisor decide intervenir principalmente por errores
repetidos, contradicciones, deriva o acciones peligrosas. Eso no cubre bien las
herramientas largas que no producen un error claro, pero que pueden estar
bloqueadas, degradadas o siguiendo un camino sin sentido.

El caso Caffe lo demostro: el agente compilo correctamente, pero quedo mas de
media hora descargando CIFAR-10 desde Toronto a unos 60-70 KB/s. La ejecucion
termino en `AgentTimeoutError` sin que el Supervisor tuviera una regla para
evaluar si convenia esperar, buscar otro mirror o intervenir.

## Deuda tecnica

El Supervisor debe observar duracion, progreso y falta de progreso de las
herramientas largas, ademas de los errores repetidos.

Debe persistir al menos:

- inicio de cada llamada de herramienta larga;
- ultima evidencia de progreso, como bytes, salida o cambio de estado;
- duracion acumulada y tiempo sin progreso;
- intentos equivalentes y sus resultados;
- decisiones del Supervisor sobre continuar, esperar, intervenir o abortar.

## Comportamiento esperado

- Una herramienta larga activa no debe considerarse automaticamente bloqueada.
- Si existe progreso, el Supervisor puede decidir continuar aunque la llamada
  sea lenta.
- Si no existe progreso durante un umbral, debe revisar si conviene enviar una
  instruccion correctiva.
- Una descarga lenta debe poder compararse contra un tiempo estimado y contra
  alternativas razonables, como otro mirror o una dependencia precargada.
- El Supervisor debe intervenir aunque no haya dos errores identicos cuando la
  evidencia indique espera improductiva, perdida de tiempo o agotamiento del
  presupuesto de ejecucion.
- Un timeout proximo debe ser una senal operativa para revisar el plan, no solo
  un resultado posterior al fallo.

## Criterios de aceptacion futuros

- El contexto de REVIEW incluye `started_at`, `last_progress_at`, `elapsed` y
  `stalled_for` para la herramienta activa.
- El Supervisor puede distinguir entre compilacion activa, descarga con
  progreso, descarga estancada y retry sin avance.
- Hay pruebas para una descarga lenta y para una herramienta sin progreso.
- Una intervencion puede recomendar cambiar de estrategia sin abortar por
  defecto.
- La memoria persistente conserva la observacion y la decision para evitar
  repetir esperas improductivas en tareas futuras.
