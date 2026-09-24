# Organiza 1.1.45

- Corrige falha de inicialização no Render quando DATABASE_URL usa `postgresql+psycopg://`.
- Adiciona o driver `psycopg` 3 com binários às dependências.
- Mantém `psycopg2-binary` para compatibilidade com URLs PostgreSQL antigas.
