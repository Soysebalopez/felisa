# claude.ai — conectar tu memoria

Felisa expone un **MCP server** que claude.ai puede consultar al inicio de cada
conversación. Resultado: Claude conoce tus decisiones, patrones y contexto sin
que tengas que copiar/pegar nada.

## Por qué

- **Lectura transparente**: pedile a Claude "ayudame a diseñar el módulo de auth de MiApp" y él automáticamente llama `search_memories` sobre tu base.
- **No filtra a nadie más**: el MCP solo responde a tu chat de claude.ai (autenticado con un bearer token que solo vos tenés).

## Setup

### 1. Deployar el MCP en Railway

Click acá: **[Deploy to Railway](https://railway.app/template/felisa)** *(URL provisional)*.

Railway te pide:

- **`ANTHROPIC_API_KEY`** — la misma que usás localmente.
- **`CLOUDFLARE_ACCOUNT_ID`** + **`CLOUDFLARE_API_TOKEN`** — embeddings.
- **`FELISA_API_TOKEN`** — un token random que vas a usar para autenticar desde claude.ai. Generá uno con:
  ```bash
  python -c "import secrets; print(secrets.token_urlsafe(32))"
  ```

El template incluye Postgres con pgvector preconfigurado (si querés usar el mismo Postgres que tu instancia local, en `DATABASE_URL` pegá tu URL existente — Railway lo respeta).

Railway te da una URL pública tipo `https://felisa-mcp-production.up.railway.app`. Copiala.

### 2. Aplicar el schema

Una vez deployado, abrí la consola de Railway en el servicio y corré:

```bash
psql $DATABASE_URL -f sql/001_init.sql
psql $DATABASE_URL -f sql/002_oauth_tokens.sql
```

(O si tu DATABASE_URL apunta a Postgres que ya tenés con el schema, este paso es no-op.)

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
