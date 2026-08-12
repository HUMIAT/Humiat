# Humiat ID — Fase 1

## Objetivo
Um único login em `humiat.com.br` para abrir os produtos Humiat autorizados por empresa.

Perfis iniciais:
- `ADMIN_HUMIAT`: pode administrar todas as empresas, usuários e produtos.
- `ADMIN_EMPRESA`: enxerga apenas as empresas vinculadas a ele e seus produtos ativos.

Produtos cadastrados automaticamente:
- Connect
- LokaFest
- SolVoz
- Organiza

## Rotas
- `/entrar` — login Humiat ID
- `/painel` — painel central
- `/admin-humiat` — administração central (somente ADMIN_HUMIAT)
- `/sair` — encerra a sessão
- `/api/humiat/sso/validar` — validação servidor-servidor de ticket SSO

A antiga `/area-restrita/login` redireciona para `/entrar`.

## Variáveis obrigatórias no Render
Configure antes do primeiro deploy:

```text
HUMIAT_ADMIN_EMAIL=seu-email@dominio.com
HUMIAT_ADMIN_NOME=Seu Nome
HUMIAT_ADMIN_SENHA=UMA-SENHA-FORTE
HUMIAT_SSO_SECRET=UM-SEGREDO-LONGO-ALEATORIO
```

`HUMIAT_SSO_SECRET` deve ser exatamente o mesmo nos produtos que recebem SSO, começando pelo SolVoz.

## SolVoz
O Humiat emite ticket de uso único e redireciona para:

`https://www.solvoz.com.br/_sv/sso/humiat?humiat_ticket=...`

O SolVoz valida o ticket diretamente com o Humiat e cria sua sessão local. O cliente então vê um painel com:
- Editar Início
- Editar Site (quando o plano inclui Home)
- Ver Catálogo
- Ver Site

Sem pedir outra senha.

## Connect e LokaFest
A emissão SSO já está preparada. O receptor de cada produto será implantado quando atualizarmos o projeto correspondente.
