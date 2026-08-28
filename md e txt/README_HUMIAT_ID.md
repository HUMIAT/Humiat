# Humiat ID — Fase 1

## Objetivo
Um único login em `humiat.com.br` para abrir os produtos Humiat autorizados por empresa.

Perfis iniciais:
- `ADMIN_HUMIAT`: pode administrar todas as empresas, usuários e produtos.
- `CLIENTE_EMPRESA`: enxerga apenas as empresas vinculadas a ele e seus produtos ativos.

Produtos cadastrados automaticamente:
- Connect
- LokaFest
- SolVoz
- Organiza

## Rotas
- `/entrar` — login Humiat ID
- `/painel` — painel único; para `ADMIN_HUMIAT` abre a administração completa e para `CLIENTE_EMPRESA` abre somente os produtos da própria empresa
- `/admin-humiat` — compatibilidade: redireciona o Administrador Humiat para `/painel`
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


## Entrada única do site
- `/acesso` é a tela aberta pelo botão **Entrar** do site institucional.
- **Área do Cliente** → `/entrar` (Humiat ID).
- **Área Restrita** → `/area-restrita/login` (login tradicional do Organiza).
- Os dois logins permanecem independentes nesta fase.

## Fase 1.0.4
- Remove o campo "Usuário Organiza" do cadastro Humiat ID.
- Usuários do Organiza continuam independentes e usam o login tradicional do Organiza.
- Adiciona edição de usuário Humiat: nome, e-mail, perfil, empresa e status.
- Acesso da Empresa passa a exigir empresa no cadastro e na edição, evitando usuários sem vínculo.
- Administrador Humiat não precisa de empresa.


## Fase 1.0.5 — ADM unificado
- O Administrador Humiat passa a trabalhar em uma única tela em `/painel`.
- A empresa selecionada fica na lateral e todas as ações da direita pertencem a ela.
- `Nova empresa` e `Clonar empresa` são operações separadas e explícitas.
- A clonagem central copia os acessos Humiat e, quando SolVoz está ativo, clona também Início, Home, redes, logo e cores no SolVoz.
- O clone nasce inativo para revisão; ao ativar no Humiat, o status de publicação do SolVoz é sincronizado.
- Ativar SolVoz em uma empresa garante automaticamente a existência do cadastro correspondente no produto.
- Cores e tema do SolVoz podem ser ajustados pelo painel Humiat.
- O QR Code do catálogo aparece para o Administrador Humiat e também para o usuário da empresa.
- Criação/edição de usuários continua no mesmo painel, sem abrir uma área administrativa separada.

### Integração administrativa SolVoz
A clonagem, criação, cores e status usam chamadas servidor-servidor protegidas por `HUMIAT_SSO_SECRET`.
O mesmo valor precisa estar configurado no Humiat e no SolVoz.

Variáveis recomendadas:

```text
HUMIAT_SOLVOZ_URL=https://www.solvoz.com.br
HUMIAT_SOLVOZ_SSO_URL=https://www.solvoz.com.br/_sv/sso/humiat
HUMIAT_SOLVOZ_API_TIMEOUT=8
```

Para publicar esta fase, implante primeiro o SolVoz 2.3.6 e depois o Humiat ID 1.0.5.
