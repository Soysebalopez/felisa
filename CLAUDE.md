# Felisa — Contexto para Claude Code

Sistema de memoria persistente para Claude. Daemon Python local + Railway Postgres + comando `mem` desde terminal + MCP custom que Claude.ai consulta al inicio de cada conversacion.

## Fuente de verdad

- **Linear (truth)**: proyecto `Felisa — Sistema de memoria persistente para Claude` en team `Whitebay Products` (WHI). Issues y milestones reflejan estado real. Actualizar status + comentarios a medida que se avanza.
  - URL: https://linear.app/white-bay/project/felisa-sistema-de-memoria-persistente-para-claude-5e85437b7801
- **Documentos en Linear**:
  - "Diseño y Arquitectura — Felisa" — vision, fases, schema, prompts, stack
  - "Prerequisites — Felisa" — checklist inicial (cerrado para Fase 1)

## Fases del proyecto

Numeradas en el documento de diseño. Cada fase corresponde a un milestone en Linear.

| Fase | Que hace | Milestone Linear |
|------|----------|------------------|
| 1 | Core: daemon + Postgres + `mem` desde terminal | Fase 1 — Core (100%) |
| 2 | Agente conversacional `felisa` | Fase 2 — Agente conversacional (100%) |
| 3 | MCP custom (FastAPI en Railway) + integracion Claude.ai | Fase 3 — MCP (100%) |
| 4 | Telegram bot + Whisper para voz | Fase 4 — Telegram + Whisper (100%) |
| 5 | Deteccion automatica de patrones | pendiente |
| 6 | Weekly synthesis con cruce de proyectos | pendiente |
| 7 | Hook de Claude Code para captura automatica | pendiente |
| 8 | Polish + script de instalacion | pendiente |

## Stack

| Componente | Herramienta |
|------------|-------------|
| Lenguaje | Python 3.12 |
| Package manager | uv |
| Build | hatchling |
| DB | Railway PostgreSQL 18.3 + pgvector 0.8.2 (indice HNSW) |
| Embeddings | Cloudflare Workers AI `@cf/baai/bge-small-en-v1.5` (384 dim) |
| LLM estructuracion | Claude API Haiku (anthropic) |
| MCP custom | FastAPI (deploy Railway) — Fase 3 |
| Captura voz | Groq Whisper — Fase 4 |
| Captura mobile | Telegram bot — Fase 4 |
| Daemon | LaunchAgent macOS |

## Estructura del repo

```
felisa/
  core/
    config.py          # leer credenciales del Keychain
    db.py              # conexion + operaciones Postgres (memories + spaces CRUD)
    embeddings.py      # cliente Ollama (nomic-embed-text, 768 dim)
    structuring.py     # llamada a Haiku para clasificar y limpiar
    pipeline.py        # structure + embed + insert (usado por CLI y daemon)
    queue.py           # ~/.felisa/queue.json para items offline
    agent.py           # agente conversacional con tool use (Sonnet 4.6)
    models.py          # Memory, Space, SearchHit, StructuredMemory
    prompts/
      structure.md     # prompt para Haiku (estructuracion)
      agent.md         # prompt para Sonnet (agente)
  cli/
    mem.py             # comando `mem` (captura/buscar/listar/cola)
    felisa.py          # comando `felisa` (loop + one-shot)
  daemon/
    main.py            # daemon background (drainer + bot Telegram, asyncio)
  mcp/
    server.py          # MCP server con tools (Fase 3, deploy Railway)
    oauth_provider.py
  telegram/
    api.py             # cliente HTTP minimal a la Bot API (httpx async)
    bot.py             # TelegramBot: long polling + filtro chat_id + pipeline
    whisper.py         # transcribe(audio_bytes, mime) via Groq Whisper
sql/
  001_init.sql         # schema spaces + memories + pgvector + HNSW
scripts/
  com.felisa.daemon.plist
  install-daemon.sh
  uninstall-daemon.sh
tests/                 # 82 tests, todos verde
```

## Credenciales

Todas en Keychain de macOS. Nunca en `.env`, codigo o variables de entorno.

| Servicio | Slot Keychain |
|----------|---------------|
| Anthropic API | `felisa-anthropic-key` |
| Telegram bot token | `felisa-telegram-token` |
| Telegram chat ID | `felisa-telegram-chat-id` |
| Groq API | `felisa-groq-key` |
| Cloudflare account ID | `felisa-cf-account-id` |
| Cloudflare API token | `felisa-cf-token` |
| MCP server bearer token | `felisa-mcp-token` |

Lectura desde Python:
```python
import subprocess
def read_keychain(service: str, account: str = "felisa") -> str:
    return subprocess.run(
        ["security", "find-generic-password", "-s", service, "-a", account, "-w"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
```

## Infra Railway

- Proyecto: `Felisa` (ID `20203777-affb-4b66-a6c8-4013cf51f1ad`)
- Environment: `production`
- Servicio Postgres: `Postgres` — provee `DATABASE_URL` (interna) y `DATABASE_PUBLIC_URL` (externa para dev local)
- pgvector 0.8.2 instalado
- Tablas: `spaces`, `memories` (con indice ivfflat sobre `embedding`)
- Seeds: `global` (es_global=true), `whitebay`, `simplistic`

Conexion local para desarrollo:
```bash
railway run --service Postgres -- <comando-que-necesita-DATABASE_URL>
# o exportar manualmente:
export DATABASE_URL=$(railway variables --service Postgres --json | jq -r .DATABASE_PUBLIC_URL)
```

## Tipos de memoria

Asignados por Haiku al estructurar (`tipo` column). Definicion completa en el documento de diseño en Linear:

- `decision_tecnica` — decision de stack/arquitectura
- `patron` — solucion reutilizable entre proyectos
- `framework` — regla de comportamiento para Claude autonomo
- `modo_trabajo` — preferencias de trabajo del usuario
- `contexto_proyecto` — estado y decisiones de un proyecto
- `global` — info personal y preferencias universales

## Arquitectura de captura

Modelo simplificado vs el doc de Linear original (no socket UNIX al daemon):

```
mem "texto"
    │
    ▼
pipeline.process()        ◄── sincrono (~2s)
    │  structure → embed → insert
    │
    ├─ exito → memoria guardada en Postgres
    │
    └─ fallo (Ollama/Anthropic/DB caidos) → queue.enqueue() → cola offline

daemon
    │
    ▼
loop cada 60s
    │  queue.list_pending()
    │  para cada item: pipeline.process(...)
    │  exito → queue.remove()
    │  fallo → queue.update() con attempts+1
```

## Workflow

1. Ver Linear para el siguiente issue de la fase actual
2. Crear branch siguiendo `git branch name` que Linear sugiere
3. Implementar
4. Actualizar status del issue en Linear (`In Progress` → `In Review` → `Deployed`)
5. Comentar en el issue cualquier desviacion del plan o decision relevante
6. Commit + PR (si aplica) — convencion de Whitebay

## Estado actual

- **Fase 1 completa** (commit `062a268`): captura sincrona con `mem`, daemon corriendo como LaunchAgent retrying la cola offline, 37 tests
- **Fase 2 completa** (commit `ee90294`): agente `felisa` con tool use sobre 8 tools (CRUD spaces + search/list), 24 tests nuevos
- **Fase 3 completa** (commit `6611621`): MCP server custom desplegado en Railway con OAuth, claude.ai consulta memoria automaticamente
- **Fase 4 completa**: bot Telegram con long polling + Groq Whisper, integrado al daemon como tarea async. Captura desde el movil (texto y voz). 21 tests nuevos.

Tests totales: 82 pasando. DB en Railway: 3 espacios (global/whitebay/simplistic).

## Comandos utiles

```bash
mem "texto"                     # capturar memoria (sincrono ~2s)
mem buscar "consulta"           # busqueda semantica
mem listar [--espacio X]        # ultimas 20
mem cola                        # ver pendientes en cola offline

felisa                          # loop conversacional persistente
felisa "mensaje"                # one-shot
felisa-daemon --once            # drenar cola manualmente (sin Telegram)
felisa-daemon                   # daemon en foreground: cola + bot Telegram
felisa-daemon -v                # con logs a stderr ademas de daemon.log

# Reiniciar el LaunchAgent despues de cambiar el codigo:
launchctl kickstart -k "gui/$(id -u)/com.felisa.daemon"

scripts/install-daemon.sh       # (re)instalar LaunchAgent
scripts/uninstall-daemon.sh
```

## Convenciones

- **Sin emojis** en codigo, comentarios, README, ni mensajes de commit (salvo que se pidan explicitamente).
- **Tildes y enies correctos** en strings de UI/output del usuario.
- **Comentarios solo cuando el "por que" no es obvio** — el "que" lo dice el codigo.
- **Errores claros, sin silenciar**: si falta Ollama, decir "ollama no responde en :11434", no fallar silenciosamente.
- **Idempotente cuando se pueda**: re-correr scripts no debe romper estado.
