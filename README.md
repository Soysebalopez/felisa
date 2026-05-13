# Felisa

Sistema de memoria persistente para Claude. Daemon local en Mac que captura decisiones y contexto, los sincroniza con Railway (Postgres + pgvector) y los expone via MCP para cualquier conversacion de Claude.

## Estructura

```
felisa/
  core/          # db, embeddings, structuring, modelos compartidos
  cli/           # comandos `mem` (captura rapida) y `felisa` (agente)
  daemon/        # proceso background (LaunchAgent)
  mcp/           # FastAPI que Railway sirve como MCP custom
sql/             # migraciones schema
scripts/         # instalador y utilidades
tests/
```

## Fases

Ver documento "Diseño y Arquitectura — Felisa" en Linear (proyecto WHI).
