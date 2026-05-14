# claude.ai — conectar tu memoria

Felisa expone un **MCP server** que claude.ai puede consultar al inicio de cada
conversación. Resultado: Claude conoce tus decisiones, patrones y contexto sin
que tengas que copiar/pegar nada.

## Por qué

- **Lectura transparente**: pedile a Claude "ayudame a diseñar el módulo de auth de MiApp" y él automáticamente llama `search_memories` sobre tu base.
- **No filtra a nadie más**: el MCP solo responde a tu chat de claude.ai (autenticado con un bearer token que solo vos tenés).

## Setup

### 1. Deployar el MCP en Railway

[![Deploy on Railway](https://railway.com/button.svg)](https://railway.com/new/template?template=https%3A%2F%2Fgithub.com%2FSoysebalopez%2Ffelisa)

Click el botón. Railway:
- Clona el repo `Soysebalopez/felisa` en un proyecto nuevo.
- Detecta `railway.toml` y crea un servicio que corre `uv run felisa-mcp-server`.

Después del primer build (probablemente va a fallar por falta de env vars y DATABASE_URL — esperable):

#### a) Agregar Postgres

En la UI del proyecto: **+ New → Database → PostgreSQL**. Railway crea el servicio y expone `DATABASE_URL` internamente. Vinculalo al servicio `felisa`:

- Click en el servicio `felisa` → **Variables** → **+ New → Reference variable** → seleccioná `DATABASE_URL` del servicio Postgres.

#### b) Setear el resto de variables

En **Variables** del servicio `felisa`, agregá:

- **`ANTHROPIC_API_KEY`** — la misma que usás localmente.
- **`CLOUDFLARE_ACCOUNT_ID`** + **`CLOUDFLARE_API_TOKEN`** — embeddings.
- **`FELISA_API_TOKEN`** — random, vas a usarlo para autenticar desde claude.ai. Generá:
  ```bash
  python -c "import secrets; print(secrets.token_urlsafe(32))"
  ```
- **`MCP_PUBLIC_URL`** — la URL pública que Railway te asigna al servicio (`Settings → Networking → Generate Domain`). Algo tipo `https://felisa-production-XXXX.up.railway.app`. Pegala completa con `https://` y sin slash final.

Trigger re-deploy (`Deployments → Redeploy`).

### 2. Aplicar el schema

Una vez que el servicio está corriendo, abrí la consola del Postgres en Railway (`Postgres service → Data → Query`) y pegá el contenido de:

```bash
sql/001_init.sql
sql/002_oauth_tokens.sql
```

(Los podés copiar/pegar directo, o usar `railway connect Postgres` desde tu CLI local apuntando al proyecto que recién creaste.)

### 3. Registrar el MCP en claude.ai

1. Andá a [claude.ai](https://claude.ai) → **Settings** → **Integrations** → **Add custom integration**.
2. **URL**: `https://TU-URL.up.railway.app/mcp` (con `/mcp` al final).
3. **Auth**: OAuth. claude.ai te redirige a `https://TU-URL/login`.
4. En la pantalla de login, pegá tu `FELISA_API_TOKEN`. Confirmá.
5. claude.ai obtiene un token OAuth y mantiene la sesión.

### 4. Configurar el profile de claude.ai

**Settings → Profile**, pegá:

```
Al inicio de cada conversación, consultá mi memoria personal vía el MCP de
Felisa para conocer mi contexto: proyectos, decisiones técnicas, patrones
reutilizables, modo de trabajo. Usá las tools `list_recent_memories` y
`search_memories` cuando tenga sentido. Solo guardá cosas via `create_memory`
si te lo pido explícitamente ("recordá esto", "guardá que", "anotá").
```

### 5. Probar

Abrí un chat nuevo en claude.ai y pedile:

> ¿Qué sabes sobre mí?

Claude debería llamar `list_recent_memories` y resumir tus memorias globales y de modos de trabajo.

## Seguridad

- **Solo vos tenés el `FELISA_API_TOKEN`**. Sin él, claude.ai (o cualquiera) no puede autenticar contra tu MCP.
- **Si rotás el token** (regenerás y actualizás `FELISA_API_TOKEN` en Railway), las sesiones de claude.ai existentes se invalidan en su próximo refresh.
- El MCP **valida el bearer token de claude.ai** en cada request — sin token válido, las tools no responden.
- DNS rebinding protection + CORS whitelist activos por defecto (`MCP_ALLOWED_HOSTS`, `MCP_ALLOWED_ORIGINS`).

## Costos

- Railway: el plan free tier cubre el MCP server fácil. Postgres también si lo usás solo personal.
- Cloudflare Workers AI: las consultas embeddings desde el MCP son gratis hasta cierto volumen.
- Anthropic: las llamadas a Haiku/Sonnet desde claude.ai usan **tu cuenta**, no la del MCP. El MCP no consume tokens por sí solo.

## Troubleshooting

**claude.ai dice "integration unreachable"**

Verificá la URL: tiene que terminar en `/mcp`. Y que Railway esté arriba: `curl https://TU-URL/`.

**"401 Unauthorized" al autenticar**

El `FELISA_API_TOKEN` que pegaste no coincide con el del Railway env vars. Andá a Railway → tu servicio → Variables y verificá.

**Las tools no devuelven nada**

Verificá que la DB tenga datos: `psql $DATABASE_URL -c "select count(*) from memories;"`. Si está vacía, capturá algo desde tu instancia local primero (`mem "test"`).

**Quiero borrar la integración**

claude.ai → Settings → Integrations → tu integración → Remove. Después en Railway podés borrar el servicio.

---

## Claude Code (CLI en terminal)

Si usás [Claude Code](https://claude.com/claude-code) en terminal, podés conectar tu MCP de Felisa para que cada sesión nueva tenga acceso a tu memoria.

### 1. Registrar el MCP

```bash
claude mcp add --transport http --scope user felisa https://TU-URL.up.railway.app/mcp
```

Reemplazá `TU-URL.up.railway.app` con la URL pública de tu deploy de Railway.

### 2. Autenticar

Adentro de una sesión de Claude Code, mandá:

```
/mcp
```

Te va a guiar por el flujo OAuth: te pide tu `FELISA_API_TOKEN`, lo pega contra el `/login` del MCP, y mantiene la sesión.

### 3. Que Claude use la memoria en cada sesión

Agregá este bloque a tu `~/.claude/CLAUDE.md` (instrucciones globales que Claude Code lee siempre):

```markdown
## Felisa memory (MCP)
- Si el MCP `felisa` está disponible en la sesión, consultalo al inicio para tener mi contexto: usá `list_recent_memories` para overview y `search_memories` cuando el tema lo amerite (proyectos, decisiones técnicas, patrones, modo de trabajo).
- Solo usá `create_memory` cuando lo pida explícitamente ("recordá esto", "guardá que", "anotá") — no guardes por iniciativa propia.
- Setup: `claude mcp add --transport http --scope user felisa https://TU-URL.up.railway.app/mcp` y autenticar con `/mcp` dentro de Claude Code (OAuth con `FELISA_API_TOKEN`).
```

Con eso, cualquier sesión de Claude Code (cualquier proyecto) tiene tu memoria de fondo. El bloque vive en tu `CLAUDE.md` personal global; no se commitea con el código del proyecto.

### Diferencia con claude.ai

| | claude.ai (web/app) | Claude Code (CLI terminal) |
|---|---|---|
| Setup | UI: Settings → Integrations | comando `claude mcp add` |
| Auth | UI redirige al login del MCP | `/mcp` adentro de la sesión |
| Instrucciones de uso | Settings → Profile | `~/.claude/CLAUDE.md` |
| Contexto que ve | Toda la conversación + tu memoria | El repo + tu memoria |

Ambos comparten el mismo MCP server. Si autenticás en los dos, ambos consultan la misma DB.
