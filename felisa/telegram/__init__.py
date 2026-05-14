"""Bot Telegram para capturar memorias desde el movil.

Corre dentro del daemon como tarea concurrente (asyncio). Long polling contra
la Bot API: sin webhook publico, sin servicio nuevo en Railway. Reusa
`pipeline.process()` y la cola offline existentes.
"""
