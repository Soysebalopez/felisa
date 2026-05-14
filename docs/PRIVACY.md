# Privacidad y modelo de amenaza

Felisa fue diseñado **single-tenant desde el día uno**. Cada usuario corre su propia instancia; no hay servidor central que pueda mezclar datos. Este documento explica las decisiones y los límites.

## Garantías por diseño

### 1. Tus credenciales nunca salen de tu computadora

- **macOS**: Anthropic, Cloudflare, Groq, Telegram tokens viven en el **Keychain del sistema**. Solo procesos corriendo bajo tu user pueden leerlos.
- **Linux**: viven en `.env.local`, que está en `.gitignore`. Permisos `0600` (solo tu user lo lee).
- El código NUNCA tiene defaults nuestras. NUNCA hay claves hardcodeadas.
- Los logs **redactan el token de Telegram** automáticamente (filter en `daemon/main.py`).

### 2. Tu base de datos es solo tuya

- No hay tabla `user_id` en `memories`. Una DB = un usuario.
- No hay tracking, no hay analytics, no hay envío de datos a ningún servidor que no sea el que vos configuraste.
- La búsqueda vectorial corre **en tu Postgres**. Las queries no pasan por terceros.

### 3. Los providers externos ven distintas porciones de tus datos

| Provider | Qué ve | Política |
|---|---|---|
| **Anthropic** (Haiku) | El texto de la memoria a estructurar | [Política](https://www.anthropic.com/legal/privacy). Los API calls no se usan para entrenar por defecto. |
| **Anthropic** (Sonnet) | Tus mensajes al agente + el contexto que el agente lee | Idem. |
| **Cloudflare Workers AI** | El texto a embedar | [Política](https://www.cloudflare.com/privacypolicy/). No se persiste. |
| **Groq** | El audio a transcribir | [Política](https://groq.com/privacy-policy/). |
| **Telegram** | Tus mensajes al bot | [Política](https://telegram.org/privacy). Los mensajes con bot no son E2EE — Telegram los tiene en sus servers. |
| **Railway** | Si lo usás como Postgres, ve los datos at rest | [Política](https://railway.app/legal/privacy). |
| **claude.ai** | Las queries que hace contra tu MCP | [Política](https://www.anthropic.com/legal/privacy). |

Si esto te importa: podés correr embeddings + LLM localmente (Ollama + sentence-transformers), pero requiere refactor del código. Hoy todos los providers están como cloud APIs.

### 4. Bot Telegram: filtro estricto por chat_id

```python
if incoming_chat_id != self._chat_id:
    log.warning("ignorando mensaje de chat ajeno %s", incoming_chat_id)
    return
```

Si alguien adivina el username del bot:
- El mensaje llega al bot (Telegram delivery normal).
- El bot lo recibe vía polling.
- Se rechaza antes de tocar el pipeline o la DB.
- No procesa, no guarda, no responde, no lee memoria.

### 5. MCP server: bearer token

- Si NO deployás el MCP, claude.ai NO puede tocar tu memoria.
- Si lo deployás, **solo vos tenés el `FELISA_API_TOKEN`**. Sin ese token nadie autentica.
- El MCP valida el bearer en cada request. Tokens viejos se invalidan al regenerar.

## Modelo de amenaza

### Lo que Felisa protege

| Amenaza | Mitigación |
|---|---|
| Alguien encuentra el username de mi bot | Filtro chat_id rechaza todo |
| Alguien sniffa mi red local | TLS en todas las APIs (Anthropic, CF, Groq, Telegram, Railway) |
| Un repo público filtró tu token | `.env.local` está en `.gitignore`. Keychain no se commitea jamás. |
| Un atacante obtiene mi DATABASE_URL | Sin las claves Anthropic + Cloudflare no puede capturar nuevas memorias. Pero puede leer las viejas — **es por eso que la DATABASE_URL es secreta**. |
| MCP redeploy en Railway pierde la sesión OAuth | `oauth_tokens` se persisten en Postgres |

### Lo que Felisa NO protege

| Amenaza | Por qué no |
|---|---|
| Acceso físico a tu Mac unlock | El Keychain está abierto. Asumimos que tu Mac es trusted. |
| Compromise de tu cuenta de Anthropic/Cloudflare/Telegram | Provider responsibility. Activá 2FA. |
| Telegram lee tus mensajes al bot | Esto es público en su política. Si te molesta, no uses Telegram. |
| Train-on-data leak en un provider | Asumimos que las políticas se respetan. Anthropic dice que API calls no entrenan por default. |
| Backup de Postgres con datos en claro | Tu Postgres = tu responsabilidad. Si te importa, encriptá at-rest. |

## Si liberás el bot a más usuarios

Hoy Felisa es single-user. **No lo conviertas en multi-tenant sin hacer cambios profundos**:

- Agregar `user_id` a todas las tablas.
- Agregar middleware de auth en el MCP que valide ownership.
- Repensar el flujo del bot Telegram (un bot por usuario).
- Audit completo del data flow.

Es un refactor grande, no algo que se hace con un toggle.

## Si encontrás un problema de seguridad

Por ahora abrí un issue en GitHub con label `security`. Si el bug permite exfiltración de datos, marcalo como tal — voy a priorizarlo.

## Resumen

- Tus claves: en tu máquina, encriptadas.
- Tus datos: en tu DB, accesible solo con tu URL.
- Tu bot: solo te responde a vos.
- Tu MCP: solo claude.ai con tu token puede consultarlo.
- Los providers cloud ven cada uno una porción funcional (texto, audio, queries), nunca todo junto.
- No hay "Felisa-as-a-service" — no hay un servidor central que pueda fallar y filtrar todo.
