"""OAuth 2.1 Authorization Server provider para Felisa.

Adaptado del `SimpleOAuthProvider` del MCP SDK. Usa `FELISA_API_TOKEN` como
password unico para autenticar al humano. Permite Dynamic Client Registration
(claude.ai genera su client_id al primer contacto).

Storage: clientes y access tokens en Postgres (tablas `oauth_clients` y
`oauth_tokens`). Los `auth_codes` y `state_mapping` viven en memoria — tienen
TTL <5min y solo importan durante un handshake; si el server reinicia en medio
el usuario reintenta.
"""

from __future__ import annotations

import os
import secrets
import time
from typing import Any

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyHttpUrl
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from felisa.mcp import oauth_storage


MCP_SCOPE = "user"
CODE_TTL_SECONDS = 300
TOKEN_TTL_SECONDS = 3600


class FelisaOAuthProvider(
    OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]
):
    """Provider con autenticacion por `FELISA_API_TOKEN`.

    El humano accede al /login, pega el token en el formulario, y la AS emite
    code → token.
    """

    def __init__(self, *, auth_callback_url: str, server_url: str):
        self.auth_callback_url = auth_callback_url
        self.server_url = server_url.rstrip("/")
        # auth_codes y state_mapping son efimeros (handshake en curso).
        self.auth_codes: dict[str, AuthorizationCode] = {}
        self.state_mapping: dict[str, dict[str, Any]] = {}

    def _felisa_token(self) -> str:
        expected = os.environ.get("FELISA_API_TOKEN", "").strip()
        if not expected:
            raise HTTPException(500, "FELISA_API_TOKEN no esta seteado en el server")
        return expected

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return oauth_storage.load_client(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        if not client_info.client_id:
            raise ValueError("No client_id provided")
        oauth_storage.save_client(client_info)

    async def authorize(
        self,
        client: OAuthClientInformationFull,
        params: AuthorizationParams,
    ) -> str:
        state = params.state or secrets.token_hex(16)
        self.state_mapping[state] = {
            "redirect_uri": str(params.redirect_uri),
            "code_challenge": params.code_challenge,
            "redirect_uri_provided_explicitly": str(params.redirect_uri_provided_explicitly),
            "client_id": client.client_id,
            "resource": params.resource,
        }
        return f"{self.server_url}/login?state={state}&client_id={client.client_id}"

    async def get_login_page(self, state: str) -> HTMLResponse:
        if not state:
            raise HTTPException(400, "Missing state parameter")
        html = f"""<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <title>Felisa — login</title>
  <style>
    body {{ font-family: -apple-system, system-ui, sans-serif; max-width: 460px;
           margin: 60px auto; padding: 24px; background: #0d0d0d; color: #e8e8e8; }}
    h1 {{ font-weight: 600; font-size: 22px; margin-bottom: 8px; }}
    p {{ color: #999; font-size: 14px; line-height: 1.5; }}
    form {{ margin-top: 24px; }}
    label {{ display: block; font-size: 13px; color: #bbb; margin-bottom: 6px; }}
    input[type=password] {{ width: 100%; padding: 12px; box-sizing: border-box;
                            background: #1a1a1a; color: #fff; border: 1px solid #2a2a2a;
                            border-radius: 6px; font-size: 14px; font-family: ui-monospace, monospace; }}
    button {{ margin-top: 16px; padding: 12px 20px; background: #4a9eff; color: #fff;
              border: none; border-radius: 6px; font-size: 14px; font-weight: 500;
              cursor: pointer; width: 100%; }}
    button:hover {{ background: #3a8ce8; }}
    .hint {{ margin-top: 16px; font-size: 12px; color: #666; }}
  </style>
</head>
<body>
  <h1>Felisa</h1>
  <p>Memoria persistente de Seba.<br>
  Estas autorizando un cliente MCP a leer tu memoria. Pega tu FELISA_API_TOKEN para continuar.</p>
  <form action="{self.server_url}/login/callback" method="post">
    <input type="hidden" name="state" value="{state}">
    <label for="token">FELISA_API_TOKEN</label>
    <input type="password" id="token" name="token" autocomplete="off" autofocus required>
    <button type="submit">Autorizar</button>
  </form>
  <div class="hint">El token esta en tu Keychain con slot <code>felisa-mcp-token</code>.</div>
</body>
</html>"""
        return HTMLResponse(content=html)

    async def handle_login_callback(self, request: Request) -> Response:
        form = await request.form()
        token = form.get("token")
        state = form.get("state")
        if not isinstance(token, str) or not isinstance(state, str):
            raise HTTPException(400, "Missing token or state")
        return RedirectResponse(
            url=await self._issue_code_after_login(token, state),
            status_code=302,
        )

    async def _issue_code_after_login(self, token: str, state: str) -> str:
        sd = self.state_mapping.get(state)
        if not sd:
            raise HTTPException(400, "Invalid state parameter")

        if not secrets.compare_digest(token.strip(), self._felisa_token()):
            raise HTTPException(401, "Token invalido")

        redirect_uri = sd["redirect_uri"]
        code_challenge = sd["code_challenge"]
        redirect_uri_provided_explicitly = sd["redirect_uri_provided_explicitly"] == "True"
        client_id = sd["client_id"]
        resource = sd.get("resource")

        assert redirect_uri and code_challenge and client_id

        new_code = f"mcp_{secrets.token_hex(16)}"
        self.auth_codes[new_code] = AuthorizationCode(
            code=new_code,
            client_id=client_id,
            redirect_uri=AnyHttpUrl(redirect_uri),
            redirect_uri_provided_explicitly=redirect_uri_provided_explicitly,
            expires_at=time.time() + CODE_TTL_SECONDS,
            scopes=[MCP_SCOPE],
            code_challenge=code_challenge,
            resource=resource,
        )
        del self.state_mapping[state]
        return construct_redirect_uri(redirect_uri, code=new_code, state=state)

    async def load_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: str,
    ) -> AuthorizationCode | None:
        return self.auth_codes.get(authorization_code)

    async def exchange_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: AuthorizationCode,
    ) -> OAuthToken:
        if authorization_code.code not in self.auth_codes:
            raise ValueError("Invalid authorization code")
        if not client.client_id:
            raise ValueError("No client_id provided")

        mcp_token = f"mcp_{secrets.token_hex(32)}"
        access = AccessToken(
            token=mcp_token,
            client_id=client.client_id,
            scopes=authorization_code.scopes,
            expires_at=int(time.time()) + TOKEN_TTL_SECONDS,
            resource=authorization_code.resource,
        )
        oauth_storage.save_token(access)
        del self.auth_codes[authorization_code.code]
        return OAuthToken(
            access_token=mcp_token,
            token_type="Bearer",
            expires_in=TOKEN_TTL_SECONDS,
            scope=" ".join(authorization_code.scopes),
        )

    async def load_access_token(self, token: str) -> AccessToken | None:
        access_token = oauth_storage.load_token(token)
        if not access_token:
            return None
        if access_token.expires_at and access_token.expires_at < time.time():
            oauth_storage.delete_token(token)
            return None
        return access_token

    async def load_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: str,
    ) -> RefreshToken | None:
        return None

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        raise NotImplementedError("Refresh tokens no soportados en v1")

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        if isinstance(token, AccessToken):
            oauth_storage.delete_token(token.token)
