# Telegram — capturar desde el móvil

El bot de Felisa corre en tu Mac (NO en Railway) con long polling. Eso significa:
no necesitás dominio público, no necesitás otro servicio en Railway, y tus
credenciales nunca salen de tu máquina.

## Por qué usar Telegram si ya tenés claude.ai

- **Voz** en movimiento: caminando, en el auto, etc. (1 toque para grabar).
- **Push notifications** (a futuro: weekly synthesis, recordatorios).
- **Share desde otras apps** (forward de tweet → captura).
- El chat te queda como journal.

claude.ai gana cuando estás chateando con Claude y querés que use tu memoria como contexto.

## Cómo crear el bot (3 minutos)

### 1. Hablale a [@BotFather](https://t.me/BotFather)

En Telegram, buscá `@BotFather` y mandale `/start`.

### 2. Crear bot nuevo

Mandá `/newbot`. Te va a pedir:

- **Nombre del bot**: el nombre visible, ej. "Felisa - mi memoria".
- **Username**: terminado en `bot`, ej. `mi_felisa_bot`. Tiene que ser único en Telegram.

BotFather te responde con un mensaje que contiene el **token** (algo como `8847650935:AAHh5gBz...`). **Copialo, lo necesitás en un paso siguiente.**

### 3. Conseguir tu `chat_id`

Mandá `/start` a [@userinfobot](https://t.me/userinfobot). Te responde con tu ID, un número como `159946020`. Copialo.

### 4. Configurar Felisa

Si todavía no corriste `install.py`:

```bash
python scripts/install.py
```

Cuando te pregunte por **Telegram bot token** y **Telegram chat ID**, pegá los valores.

Si ya está instalado y querés agregar Telegram después:

```bash
# macOS
security add-generic-password -U -s felisa-telegram-token -a felisa -w 'TU_TOKEN'
security add-generic-password -U -s felisa-telegram-chat-id -a felisa -w 'TU_CHAT_ID'
launchctl kickstart -k "gui/$(id -u)/com.felisa.daemon"
```

### 5. Hablar al bot

En Telegram, abrí tu bot (lo encontrás buscando el username que elegiste) y mandale `/start`. Después:

```
decidí usar Postgres con pgvector para MiApp
```

El bot responde algo como `Guardado · decision_tecnica · workspace`.

Probá también un mensaje de voz (5-10s describiendo algo). Whisper transcribe, agente procesa.

## Privacidad

El bot tiene un **filtro estricto por chat_id**: solo procesa mensajes del chat que configuraste. Si alguien adivina el username del bot y le escribe:

- El mensaje llega al bot (Telegram delivery normal).
- El bot lo recibe vía polling.
- El filtro lo rechaza: `WARNING ignorando mensaje de chat ajeno`.
- No procesa, no guarda, no responde.

El **token** vive solo en tu Keychain. Si lo rotás (`/revoke` en BotFather), invalidás cualquier bot impersonator.

## Trucos

- **Audio de Siri**: "Oye Siri, manda un mensaje al bot de Felisa" — Siri puede transcribir y mandar directo, evitando el botón de grabar.
- **Compartir desde Twitter/X**: tap share → Telegram → tu bot. El texto queda capturado.
- **Programado**: si querés que el bot te recuerde capturar (ej. "hoy decidiste algo?"), eso viene en Fase 7 (weekly synthesis + recordatorios).

## Troubleshooting

**El bot no responde**

```bash
tail -f ~/.felisa/daemon.log
```

Buscá `telegram bot iniciado` al arrancar. Si no aparece, falta token. Si dice `ignorando mensaje de chat ajeno`, el `chat_id` no coincide con el tuyo — verificá con [@userinfobot](https://t.me/userinfobot).

**"401" en los logs**

El token fue revocado. Andá a BotFather → `/mybots` → tu bot → API Token → `Revoke current token` → copiá el nuevo y reconfigurá.

**Mensajes viejos llegando en bulk al iniciar**

El offset persiste en `~/.felisa/telegram_offset`. Si querés saltear el histórico:

```bash
rm ~/.felisa/telegram_offset
# mandá un mensaje nuevo al bot
launchctl kickstart -k "gui/$(id -u)/com.felisa.daemon"
```
