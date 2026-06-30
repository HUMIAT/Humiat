# HUMIAT - Site Institucional

Site FastAPI da HUMIAT.

## Rodar local

```powershell
py -m pip install -r requirements.txt
py -m uvicorn app:app --reload
```

Acesse:

```text
http://127.0.0.1:8000
```

## Render

Build Command:

```bash
pip install -r requirements.txt
```

Start Command:

```bash
uvicorn app:app --host 0.0.0.0 --port $PORT
```

## Contato configurado

WhatsApp: (21) 99650-4516  
E-mail: humiat@gmail.com

## Versão

v1.1 - ajustes de responsividade mobile, WhatsApp flutuante e identidade HUMIAT.


## Versão V4.3

Inclui data/hora real no painel, foco em tarefas, ordem manual por setas, número de posição e exclusão de tarefa com regra de permissão.

## Organiza 7.0 - Produção com Neon PostgreSQL

Esta versão usa o banco automaticamente conforme o ambiente:

- Local: se `DATABASE_URL` não existir, usa `sqlite:///./organiza.db`.
- Render/Produção: se `DATABASE_URL` existir, usa PostgreSQL/Neon.

Variáveis obrigatórias no Render:

```env
DATABASE_URL=postgresql://...
SECRET_KEY=uma-chave-grande
ORGANIZA_ADMIN_NOME=Admin
ORGANIZA_ADMIN_SENHA=uma-senha-forte
```

Comando de start no Render:

```bash
uvicorn app:app --host 0.0.0.0 --port $PORT
```

Na primeira execução, o sistema cria as tabelas e cria automaticamente o administrador inicial se ainda não existir nenhum usuário.
