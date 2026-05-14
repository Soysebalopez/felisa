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
  embedding vector(384),
  created_at timestamp default now()
);

create index if not exists memories_embedding_idx
  on memories using hnsw (embedding vector_cosine_ops);

-- El unico espacio que viene por default es 'global' (preferencias e info universales).
-- Los espacios de trabajo (workspace, personal, cliente_x, ...) los crea el usuario:
--   - via el wizard `python scripts/install.py`
--   - via el agente:  felisa "creame un espacio para mi trabajo en X"
--   - via SQL directo si quiere
insert into spaces (id, nombre, descripcion, es_global) values
  ('global', 'Global', 'Reglas y preferencias universales', true)
on conflict (id) do nothing;
