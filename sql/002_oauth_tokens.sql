-- Persistencia de OAuth para el MCP server.
--
-- Sin esto, cada redeploy de Railway tira las sesiones de claude.ai porque los
-- clientes registrados via DCR y los access tokens viven en un dict en memoria
-- del proceso. Con esto, sobreviven al restart y el humano no tiene que volver
-- a pegar FELISA_API_TOKEN cada vez que pusheamos.
--
-- Idempotente: corre seguro en cada startup.

create table if not exists oauth_clients (
  client_id text primary key,
  info      jsonb       not null,
  created_at timestamptz default now()
);

create table if not exists oauth_tokens (
  token      text primary key,
  client_id  text not null,
  scopes     text[] not null default '{}',
  resource   text,
  expires_at timestamptz,
  created_at timestamptz default now()
);

create index if not exists oauth_tokens_expires_idx
  on oauth_tokens (expires_at);
