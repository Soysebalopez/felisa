# Changelog

Formato basado en [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
versioning en [SemVer](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.5.0] — 2026-05-15

### Added
- **Fase 6 — Hook de captura automatica (Claude Code)**: hook `SessionEnd` que
  analiza el transcript de cada sesion con Haiku y propone memorias para
  guardar. Las propuestas llegan al bot de Telegram con botones inline
  (Guardar / Descartar / Mas tarde) o se revisan desde terminal con
  `mem propuestas`. TTL 24h con auto-descarte.
- `felisa/core/proposals.py`: cola persistida en `~/.felisa/proposals.json`
  con file-lock; status pending/approved/rejected/expired.
- `felisa/core/hook_detection.py`: cliente Haiku conservador (filtro
  confianza >= 0.6, sesgo a falsos negativos) con prompt en
  `felisa/core/prompts/hook_detect.md`.
- `felisa/hooks/session_end.py`: entry-point del hook. Tolerante a fallos
  (loggea a `~/.felisa/hook.log` y siempre devuelve 0 para no bloquear el
  cierre de la sesion de Claude Code).
- Entry-point `felisa-hook-session-end` en `pyproject.toml`.
- Bot de Telegram: dispatcher de propuestas + handler de callback queries
  (approve/reject/defer). API extendida con `editMessageText` y
  `answerCallbackQuery`.
- Daemon: dos coroutines nuevas (`_proposals_dispatcher`, `_proposals_expirer`)
  corriendo dentro del `TaskGroup` del bot.
- Installer: paso opcional 7/8 que registra el hook en
  `~/.claude/settings.json` (backup automatico antes de modificar).
- Subcomando `mem propuestas {listar|aprobar|descartar|limpiar}`.
- Installer interactivo `scripts/install.py` para setup desde cero.
- Documentacion publica: README + QUICKSTART + TELEGRAM + CLAUDE_AI + ARCHITECTURE + PRIVACY.
- LICENSE (MIT) + CONTRIBUTING.
- GitHub Actions CI: tests + ruff en cada PR.
- Soporte de prompts personalizados via `~/.felisa/prompts/{structure,agent}.md`.
- **Soporte Linux completo**: `config.py` lee credenciales via `keyring` package
  (Secret Service / KWallet / fallback). `install.py` genera y activa systemd
  user unit (`~/.config/systemd/user/felisa.service`).

### Changed
- `Agent.user_name` ahora lee `FELISA_USER_NAME` env var (default "el usuario").
- SQL seed solo crea espacio `global`; los espacios de trabajo los crea el usuario.
- Prompts default genericos sin ejemplos personalizados.
- Strings de MCP server y CLI neutralizados ("Memoria personal persistente").

## [0.4.0] — 2026-05-14

### Added
- **Fase 4 — Telegram + Whisper**: bot de Telegram con long polling integrado
  al daemon como tarea asyncio. Captura texto + voz (Groq Whisper). Filtro
  estricto por chat_id (defensa contra mensajes ajenos).
- Agente conversacional via Telegram: cualquier mensaje pasa por
  `felisa.core.agent.Agent` con tool use (incluye `save_memory` nuevo).
- `sendChatAction(typing)` mientras el agente piensa.
- Filter de logging que redacta el token de Telegram en `daemon.log`.

## [0.3.0] — 2026-05-13

### Added
- **Fase 3 — MCP custom + claude.ai**: MCP server con FastMCP, deploy en Railway,
  OAuth 2.1 con Dynamic Client Registration. Tools de lectura sobre la memoria.
- Persistencia OAuth en Postgres (tablas `oauth_clients`, `oauth_tokens`).
- Tool `create_memory` en el MCP para escritura desde claude.ai.

## [0.2.0] — 2026-05-13

### Added
- **Fase 2 — Agente conversacional**: comando `felisa` con loop conversacional
  + one-shot. Sonnet 4.6 con tool use sobre CRUD spaces + search + list.

## [0.1.0] — 2026-05-13

### Added
- **Fase 1 — Core**: daemon Python como LaunchAgent, comando `mem`, Railway
  PostgreSQL con pgvector, embeddings via Cloudflare Workers AI (bge-small-en),
  structuring via Claude Haiku, cola offline con retry.
