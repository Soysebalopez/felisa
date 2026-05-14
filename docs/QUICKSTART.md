# Quickstart

De 0 a daemon corriendo en 5 minutos.

## Prerrequisitos

- macOS o Linux (Windows aún no soportado).
- Python 3.12+.
- [uv](https://docs.astral.sh/uv/) (`curl -LsSf https://astral.sh/uv/install.sh | sh`).
- Una cuenta de [Anthropic](https://console.anthropic.com/) con créditos (~$1/mes para uso personal).
- Una cuenta de [Cloudflare](https://dash.cloudflare.com/) (plan gratis alcanza para embeddings).
- Postgres con `pgvector`. Opciones:
  - [Railway](https://railway.app) — un click, gratis hasta cierto uso (recomendado).
  - Postgres local — `brew install postgresql; brew services start postgresql`.
  - Cualquier provider que soporte la extensión `vector`.

## Instalar

```bash
git clone https://github.com/soysebalopez/felisa.git
cd felisa
uv sync
python scripts/install.py
```

El wizard te pregunta:

1. **Credenciales** (una por una, con links a dónde obtenerlas):
   - Anthropic API key (obligatoria).
   - Cloudflare Account ID + API token (obligatorios — embeddings).
   - Groq API key (opcional — voz vía Telegram).
   - Telegram bot token + tu chat_id (opcional — bot móvil).
2. **Tu nombre** para personalizar el agente.
3. **DATABASE_URL** — Railway template o pegás la tuya.
4. **Daemon** — el wizard instala el LaunchAgent (macOS).

Las credenciales se guardan en el **Keychain de macOS** o en `.env.local` (Linux, gitignored). Nunca tocan el repo.

## Primer uso

```bash
# Captura desde terminal (rápido, ~2s)
mem "decidí usar pgvector para MiApp porque ya tengo Postgres"

# Búsqueda semántica
mem buscar "pgvector"

# Listar últimas 20
mem listar
```

```bash
# Agente conversacional (multi-turn)
felisa

> que decisiones tengo sobre MiApp?
> creame un espacio para mi cliente Acme
```

## Próximos pasos opcionales

- **Telegram** desde el celular: [`TELEGRAM.md`](TELEGRAM.md).
- **claude.ai integration**: [`CLAUDE_AI.md`](CLAUDE_AI.md).

## Troubleshooting

**`mem` no funciona / "command not found"**
Estás en el directorio del repo? Probaste `uv run mem ...`?

**"MissingCredential: No se encontro 'felisa-anthropic-key'"**
La key no quedó guardada. Re-corre `python scripts/install.py` y prestá atención al paso 2.

**"Cloudflare 401"**
El token no tiene permiso de Workers AI. Andá a [API Tokens](https://dash.cloudflare.com/profile/api-tokens), edita el token, y agregale el scope `Account > Workers AI > Edit`.

**El daemon no arranca**
Verificá los logs: `tail -f ~/.felisa/daemon.log`. Si hay `MissingCredential`, faltaron claves.

**El bot Telegram no responde**
Chequeá que el `chat_id` configurado coincida con el tuyo (mandate un mensaje a [@userinfobot](https://t.me/userinfobot) para verlo).
