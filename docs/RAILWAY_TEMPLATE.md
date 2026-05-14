# Crear el template público de Railway

Esta es la guía interna para **el autor del repo** (vos) para publicar el botón
"Deploy to Railway". Los usuarios finales solo necesitan hacer click — esta nota
es para que vos sepas qué configurar del lado de Railway.

## Pasos

1. En Railway, andá a `New Project` → `Deploy from GitHub` → seleccioná el repo `felisa`.
2. Agregá un servicio Postgres al proyecto. Railway lo provee con `pgvector` disponible (asegurate de que la imagen sea reciente).
3. Linkeá la `DATABASE_URL` del Postgres al servicio `felisa` (referencia: `${{Postgres.DATABASE_URL}}`).
4. Settealas variables que faltan (ver `railway.toml`).
5. Esperá el primer deploy. Anotá la URL pública del servicio (tipo `felisa-production.up.railway.app`).
6. Volvé a las variables y agregá `MCP_PUBLIC_URL=https://...up.railway.app`.
7. Re-deploy.
8. Una vez que el deploy esté green, en Railway:
   - `Settings` → `Template` → `Publish as Template`.
   - Configurá qué env vars son requeridas, opcionales, autogeneradas (`FELISA_API_TOKEN` con `${{secret(32)}}` por ejemplo).
   - Generá la URL pública del template (tipo `https://railway.app/template/felisa`).

9. Reemplazá el placeholder en `docs/CLAUDE_AI.md` y `README.md` con la URL real.

## Schema initial

Después del primer deploy, aplicá los SQL:

```bash
railway run psql $DATABASE_URL -f sql/001_init.sql
railway run psql $DATABASE_URL -f sql/002_oauth_tokens.sql
```

O incluí estos en un step de `nixpacks.toml` que corra en startup:

```toml
[phases.start]
cmd = "psql $DATABASE_URL -f sql/001_init.sql && psql $DATABASE_URL -f sql/002_oauth_tokens.sql && uv run felisa-mcp-server"
```

(Idempotente porque usamos `create table if not exists` y `on conflict do nothing`.)

## Healthcheck

`railway.toml` apunta a `/health`. Si el endpoint no existe en `felisa/mcp/server.py`, agregalo:

```python
@app.route("/health")
async def health(request):
    return JSONResponse({"status": "ok"})
```

(Verificar — si no existe, Railway puede reportar "no healthcheck" pero igual funciona.)
