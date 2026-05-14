# Contribuir a Felisa

Felisa es un proyecto personal abierto. Las contribuciones que ayudan al uso
de cualquiera son bienvenidas; las que solo me sirven a mi probablemente las
implemente solo.

## Setup local

```bash
git clone https://github.com/soysebalopez/felisa.git
cd felisa
uv sync
python scripts/install.py
```

`install.py` te guia para configurar credenciales, base de datos, y daemon.

## Antes de un PR

```bash
uv run pytest -q
uv run ruff check
```

Algunos tests requieren credenciales reales (Anthropic, Cloudflare). Esos
tests se saltean automaticamente si faltan las creds — no rompen la suite.

## Convenciones

- **Sin emojis** en codigo, comentarios, commits, salvo que se pidan.
- **Tildes y enies correctos** en strings de UI.
- **Comentarios solo cuando el "por que" no es obvio.**
- **Errores claros**: si Ollama esta caido, decirlo; no fallar silenciosamente.
- **Idempotente cuando se pueda**: re-correr scripts no debe romper estado.
- **Sin secretos en codigo o commits**. Todo va al Keychain o env vars.

## Estructura

Ver `docs/ARCHITECTURE.md`.

## Reportar un issue

Si encontras un bug o queres pedir una feature, abrí un issue en GitHub
describiendo:

1. Que esperabas.
2. Que paso.
3. Como reproducirlo (comando exacto, output).
4. Tu OS y version de Python.

## Privacidad

Si tu issue incluye logs, asegurate de que no haya secretos pegados. Los
logs de Felisa ya redactan el token de Telegram, pero podes tener pegado
algo de otra parte por error.
