# Arquitectura

Felisa es **single-tenant**: cada usuario corre su propia instancia con su propia DB y sus propias credenciales. No hay servidor central, no hay multi-tenant, no hay cuentas. La separación entre usuarios es por construcción, no por configuración.

## Componentes

```
┌─────────────────────────────────────────────────────────────────────┐
│                          Tu Mac / Tu Linux                          │
│                                                                     │
│   ┌────────┐   ┌──────────┐   ┌──────────────────────────────┐     │
│   │  mem   │   │  felisa  │   │       felisa-daemon          │     │
│   │ (CLI)  │   │  (CLI    │   │  ┌────────┐  ┌────────────┐  │     │
│   └───┬────┘   │  agente) │   │  │ drainer│  │ telegram   │  │     │
│       │        └────┬─────┘   │  │ (cola) │  │ bot (poll) │  │     │
│       │             │         │  └────┬───┘  └─────┬──────┘  │     │
│       │             │         └───────┼────────────┼─────────┘     │
│       │             │                 │            │               │
│       │             ▼                 ▼            ▼               │
│       │     ┌───────────────────────────────────────────┐         │
│       └────►│  felisa.core.pipeline                     │         │
│             │  structure(Haiku) → embed(CF) → insert    │         │
│             └────────────┬──────────────────────────────┘         │
│                          │                                         │
└──────────────────────────┼─────────────────────────────────────────┘
                           │
                           ▼
                ┌─────────────────────────┐         ┌──────────────────┐
                │  Postgres + pgvector    │         │  Anthropic API   │
                │  (Railway o local)      │ ◄───────│  + Cloudflare    │
                │  spaces, memories       │         │  + Groq          │
                └────────────┬────────────┘         └──────────────────┘
                             │
                             ▼
                  ┌─────────────────────────┐       ┌──────────────────┐
                  │  felisa-mcp-server      │ ◄─────│   claude.ai      │
                  │  (Railway, opcional)    │       │  (con MCP        │
                  │  OAuth + tools          │       │   integration)   │
                  └─────────────────────────┘       └──────────────────┘
```

## Data flow

### Captura desde CLI (`mem "texto"`)

1. `mem` valida creds y manda el texto al pipeline sincrónicamente.
2. `structuring.structure()` llama Haiku con el prompt de `prompts/structure.md` (o `~/.felisa/prompts/structure.md` si existe).
3. `embeddings.embed()` llama Cloudflare Workers AI → vector de 384 dim.
4. `db.insert_memory()` inserta en Postgres.
5. Si **algo falla** (Cloudflare timeout, Anthropic error, DB down) → `queue.enqueue()` lo deja en `~/.felisa/queue.json`.
6. El **daemon** drena la cola cada 60s con `pipeline.process()`.

### Captura desde Telegram

1. Bot recibe el mensaje vía long polling (`getUpdates`).
2. **Filtro estricto**: si `chat.id != TELEGRAM_CHAT_ID`, ignorar.
3. Si es voz: `getFile` → descargar → `whisper.transcribe()` → texto.
4. Texto va al agente (`Agent.chat()`) que decide:
   - `save_memory(texto)` → llama pipeline → confirma.
   - `search_memories(query)` → resume y responde.
   - Casual → responde sin guardar.
5. `sendMessage` con la respuesta del agente.
6. Offset persistido en `~/.felisa/telegram_offset`.

### Consulta desde claude.ai

1. claude.ai abre un chat. El profile incluye instrucciones de consultar Felisa.
2. Claude llama una tool del MCP (`list_recent_memories`, `search_memories`, etc.).
3. MCP autentica el bearer token (OAuth 2.1).
4. MCP llama `db.search_memories()` o equivalente.
5. Devuelve JSON con las memorias relevantes.
6. Claude las usa como contexto en su respuesta.

## Persistencia

| Datos | Dónde | Por qué |
|---|---|---|
| Memorias + spaces | Postgres (tablas `memories`, `spaces`) | Storage principal con búsqueda vectorial |
| Cola offline | `~/.felisa/queue.json` | Persiste items que fallaron, para reintento |
| Telegram offset | `~/.felisa/telegram_offset` | Evita reprocesar updates entre reinicios del daemon |
| OAuth clients/tokens | Postgres (tablas `oauth_clients`, `oauth_tokens`) | Para que claude.ai mantenga sesión entre redeploys del MCP |
| Credenciales | Keychain (Mac) o `.env.local` (Linux, gitignored) | Nunca tocan el repo |
| Logs | `~/.felisa/daemon.log` con rotación (5MB, 3 backups) | Token de Telegram redactado automáticamente |
| Prompts personalizados | `~/.felisa/prompts/{structure,agent}.md` | Override sobre el default del paquete |

## Espacios (multi-workspace)

Una memoria pertenece a un `space_id`. El seed inicial trae solo `global` (preferencias e info universales). Los espacios extra los crea el usuario:

- Vía installer.
- Vía CLI: `felisa "creame un espacio para mi trabajo en Acme"`.
- Vía SQL directo.

Haiku usa la lista de espacios disponibles (cargada en el system prompt en cada captura) para clasificar. No inventa espacios que no existan.

## Por qué no hay cuentas / multi-tenant

Cada cosa decisiva está aislada:

- DB: cada instalación = una DATABASE_URL = una DB con datos de UN usuario.
- Embeddings + LLM: cada instalación tiene sus claves; los providers no comparten datos entre cuentas.
- MCP: cada usuario que quiera claude.ai integration deploya su propio MCP con su propio `FELISA_API_TOKEN`.

Esto **simplifica el código** (no hay `WHERE user_id = ?` en ningún query) y garantiza que **no podemos accidentalmente mezclar datos**.

## Componentes opcionales

- **Telegram bot**: solo si configurás `felisa-telegram-{token,chat-id}` + `felisa-groq-key` (para voz).
- **MCP en Railway**: solo si querés claude.ai integration. La CLI y el daemon funcionan sin él.
- **LaunchAgent**: el daemon puede correr en foreground (`felisa-daemon -v`) si preferís.

## Modelo de coste (uso personal típico)

| Componente | Costo aproximado |
|---|---|
| Anthropic API (Haiku + Sonnet) | ~$1-3/mes capturando 5-10 memorias/día + ~50 consultas via claude.ai |
| Cloudflare Workers AI | gratis hasta varios miles de embeddings/día |
| Groq Whisper | ~$0.10/mes con uso de voz moderado |
| Railway Postgres + MCP | gratis en el plan starter, o ~$5/mes con un poco de uso |
| Telegram | gratis |

Total: **~$5-10/mes** con todas las superficies activas.
