# Felisa

[![CI](https://github.com/Soysebalopez/felisa/actions/workflows/ci.yml/badge.svg)](https://github.com/Soysebalopez/felisa/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Sistema de memoria persistente para Claude. Captura decisiones, patrones y contexto
de trabajo desde **terminal, móvil (voz/texto) o claude.ai**, los estructura con
Haiku, y los hace consultables desde cualquier conversación de Claude.

> No es un asistente generalista. Es la **memoria de trabajo** que Claude lee
> automáticamente al inicio de cada chat para tener tu contexto cargado.

## Qué hace

- **`mem "decidí usar pgvector para MiApp"`** — captura una memoria desde terminal en ~2s.
- **`felisa`** — agente conversacional con tools (`save_memory`, `search_memories`, `list_spaces`, ...).
- **Bot de Telegram** — manda texto o voz desde el celular; transcribe con Whisper, agente decide qué hacer.
- **MCP en claude.ai** — Claude consulta tu memoria al inicio de cada chat (transparente).
- **MCP en Claude Code** — el CLI de Claude lee tu memoria en cualquier sesión, en cualquier repo.

## Stack

| Componente | Herramienta |
|---|---|
| Lenguaje | Python 3.12 + uv |
| Storage | PostgreSQL + pgvector |
| Estructuración | Claude Haiku |
| Embeddings | Cloudflare Workers AI (`bge-small-en-v1.5`, 384 dim) |
| Voz | Groq Whisper (`whisper-large-v3`) |
| Agente | Claude Sonnet 4.6 con tool use |
| Daemon | LaunchAgent (macOS) / systemd --user (Linux) |

## Quickstart

```bash
git clone https://github.com/Soysebalopez/felisa.git
cd felisa
uv sync
python scripts/install.py
```

`install.py` te guía paso a paso: te pide las claves (Anthropic, Cloudflare,
opcional Groq/Telegram), te pregunta dónde queda tu Postgres, aplica el schema,
e instala el daemon (LaunchAgent en macOS, systemd `--user` unit en Linux).

Ver [`docs/QUICKSTART.md`](docs/QUICKSTART.md) para el detalle.

### Postgres + MCP en Railway (opcional, para integración con claude.ai)

[![Deploy on Railway](https://railway.com/button.svg)](https://railway.com/new/template?template=https%3A%2F%2Fgithub.com%2FSoysebalopez%2Ffelisa)

Click → Railway clona el repo, lee `railway.toml`, y crea el servicio del MCP server. Después agregás Postgres con un click (`+ New → Database → PostgreSQL`) y seteás las env vars (`ANTHROPIC_API_KEY`, `CLOUDFLARE_*`, `FELISA_API_TOKEN`, `MCP_PUBLIC_URL`). Detalle paso a paso en [`docs/CLAUDE_AI.md`](docs/CLAUDE_AI.md).

## Docs

- [`QUICKSTART.md`](docs/QUICKSTART.md) — instalación desde cero (5 min).
- [`TELEGRAM.md`](docs/TELEGRAM.md) — cómo crear el bot vía BotFather y conectarlo.
- [`CLAUDE_AI.md`](docs/CLAUDE_AI.md) — cómo deployar el MCP en Railway y conectarlo a claude.ai.
- [`ARCHITECTURE.md`](docs/ARCHITECTURE.md) — componentes y data flow.
- [`PRIVACY.md`](docs/PRIVACY.md) — separación de datos, manejo de credenciales, modelo de amenaza.

## Privacidad — corto

- Tus credenciales viven en tu **Keychain (macOS)** o **`.env.local` gitignored (Linux)**. Nunca en el código, nunca en commits.
- Tu base de datos es **solo tuya**. No hay servidor central, no hay multi-tenant.
- El bot de Telegram **solo responde a tu `chat_id`** (filtro estricto). Mensajes ajenos se ignoran.
- Si deployás el MCP, **solo vos tenés el bearer token** para conectarlo a claude.ai.

Ver [`docs/PRIVACY.md`](docs/PRIVACY.md) para el modelo de amenaza completo.

## Estado

| Fase | Qué hace | Estado |
|---|---|---|
| 1 | Core: daemon + Postgres + `mem` | Done |
| 2 | Agente conversacional `felisa` | Done |
| 3 | MCP custom + integración claude.ai | Done |
| 4 | Bot Telegram + Whisper para voz | Done |
| 5 | Public release (este) | En progreso |
| 6 | Detección automática de patrones | Pendiente |
| 7 | Weekly synthesis con cruce de proyectos | Pendiente |
| 8 | Hook de Claude Code para captura automática | Pendiente |

## Convenciones

Ver [`CONTRIBUTING.md`](CONTRIBUTING.md).

## License

[MIT](LICENSE).
