create extension if not exists vector;

create table if not exists spaces (
  id text primary key,
  nombre text not null,
  descripcion text,
  activo boolean default true,
  es_global boolean default false,
  created_at timestamp default now()
);

create table if not exists memories (
  id uuid primary key default gen_random_uuid(),
  contenido text not null,
  contenido_estructurado text,
  tipo text,
  space_id text references spaces(id),
  proyecto text,
  tags text[],
  proyectos_relacionados text[],
  embedding vector(768),
  created_at timestamp default now()
);

create index if not exists memories_embedding_idx
  on memories using hnsw (embedding vector_cosine_ops);

insert into spaces (id, nombre, descripcion, es_global) values
  ('global',    'Global',    'Reglas y preferencias universales',          true),
  ('whitebay',  'Whitebay',  'Proyectos y productos de Whitebay',          false),
  ('simplistic','Simplistic','Trabajo en Simplistic',                      false)
on conflict (id) do nothing;
