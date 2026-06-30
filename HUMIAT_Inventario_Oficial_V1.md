# HUMIAT — Inventário Oficial V1

## Objetivo

Este documento registra o produto existente hoje no projeto HUMIAT enviado em `Humiat.zip`.

Ele não propõe redesign, não altera código e não adiciona funcionalidades.  
Ele apenas transforma o que já existe em conhecimento organizado.

---

# 1. Visão Geral do Produto

O projeto atual possui duas partes principais:

## 1.1 Site Institucional HUMIAT

Porta de entrada pública da marca HUMIAT.

**Arquivo principal:** `templates/index.html`  
**Rota:** `/`

O site apresenta:

- quem é a HUMIAT;
- plataformas da HUMIAT;
- método de trabalho;
- chamada para contato;
- acesso à área restrita.

## 1.2 Sistema Organiza

Área restrita para gestão interna de projetos, departamentos/etapas e tarefas.

**Rota principal:** `/organiza`  
**Acesso:** exige login.

O Organiza permite:

- entrar na área restrita;
- visualizar tarefas do responsável;
- filtrar por responsável;
- acompanhar status das tarefas;
- criar projetos;
- criar departamentos/etapas dentro de projetos;
- criar tarefas;
- editar tarefas;
- alterar status;
- acompanhar percentual de conclusão dos projetos.

---

# 2. Mapa de Navegação

## 2.1 Fluxo Público

```text
Página Inicial
│
├── Empresa
├── Plataformas
├── Método
├── Contato
├── WhatsApp
├── E-mail
└── Área Restrita
    └── Login do Organiza
```

## 2.2 Fluxo da Área Restrita

```text
Login
│
└── Painel do Organiza
    │
    ├── Filtro por responsável
    │   ├── Junior
    │   ├── Debora
    │   └── Luiz
    │
    ├── Resumo por status
    │   ├── Aberta
    │   ├── Em andamento
    │   ├── Aguardando
    │   ├── Pronta
    │   └── Entregue
    │
    ├── Lista de tarefas do dia
    │   └── Editar tarefa
    │
    ├── Projetos ativos
    │   └── Abrir projeto
    │       │
    │       ├── Editar projeto
    │       ├── Novo departamento/etapa
    │       ├── Editar departamento/etapa
    │       ├── Nova tarefa
    │       └── Editar tarefa
    │
    ├── Novo projeto
    ├── Nova tarefa
    └── Sair
```

---

# 3. Telas Encontradas

## Tela 01 — Página Inicial HUMIAT

**Rota:** `/`  
**Arquivo:** `templates/index.html`  
**Tipo:** pública

### Objetivo

Apresentar a HUMIAT, suas plataformas e seu método de trabalho.

### Menus

- Início
- Empresa
- Plataformas
- Método
- Contato
- Área Restrita

### Ações

- `Conheça nossas plataformas`: leva para a seção Plataformas.
- `Falar com a HUMIAT`: abre WhatsApp.
- `Área Restrita`: abre login do Organiza.
- `E-mail`: abre envio de e-mail para contato.

---

## Tela 02 — Login do Organiza

**Rota:** `/area-restrita/login`  
**Arquivo:** `templates/organiza/login.html`  
**Tipo:** restrita

### Objetivo

Permitir que usuários autorizados entrem no Organiza.

### Campos

- Usuário
- Senha

### Botões

- `Entrar`: valida usuário e senha.

### Resultado

Se o login estiver correto, o usuário vai para o Painel do Organiza.  
Se estiver incorreto, a tela mostra erro.

---

## Tela 03 — Painel do Organiza

**Rota:** `/organiza`  
**Arquivo:** `templates/organiza/painel.html`  
**Tipo:** restrita

### Objetivo

Mostrar a visão diária de trabalho do responsável logado ou do responsável selecionado.

### Conteúdo

- nome do responsável exibido;
- tarefas do dia;
- resumo por status;
- tarefas por responsável;
- projetos ativos;
- percentual de progresso dos projetos.

### Ações

- `+ Novo`: cria nova tarefa.
- `Junior`, `Debora`, `Luiz`: filtram o painel por responsável.
- Cards de status: abrem lista de tarefas daquele status.
- `Ver tudo`: abre todas as tarefas do responsável.
- Clique em tarefa: abre edição da tarefa.
- `Novo projeto`: abre cadastro de projeto.
- Clique em projeto: abre detalhes do projeto.

---

## Tela 04 — Detalhes de Tarefas

**Rota:** `/organiza/detalhes/{tipo}/{valor}`  
**Arquivo:** `templates/organiza/detalhes.html`  
**Tipo:** restrita

### Objetivo

Mostrar listas filtradas de tarefas.

### Filtros possíveis

- por responsável;
- por status;
- por tarefas de hoje.

### Ações

- `Voltar`: retorna para a tela anterior.
- `Abrir`: abre a edição da tarefa.

---

## Tela 05 — Novo Projeto / Editar Projeto

**Rotas:**

- `/organiza/projetos/novo`
- `/organiza/projetos/{projeto_id}/editar`

**Arquivo:** `templates/organiza/projeto_form.html`  
**Tipo:** restrita

### Objetivo

Cadastrar ou editar um projeto.

### Campos

- Nome do projeto
- Observação

### Botões

- `Salvar projeto`: grava o projeto.
- `Voltar`: retorna para a tela anterior.

---

## Tela 06 — Projeto

**Rota:** `/organiza/projetos/{projeto_id}`  
**Arquivo:** `templates/organiza/projeto.html`  
**Tipo:** restrita

### Objetivo

Mostrar os detalhes de um projeto específico.

### Conteúdo

- nome do projeto;
- observação;
- percentual de conclusão;
- departamentos/etapas;
- tarefas do projeto.

### Ações

- `Voltar`: retorna ao painel.
- `Editar projeto`: abre edição do projeto.
- `Novo departamento/etapa`: cria nova etapa dentro do projeto.
- `Nova tarefa`: cria tarefa vinculada ao projeto.
- `Criar tarefa`: cria tarefa dentro de uma etapa específica.
- `Editar departamento/etapa`: abre edição da etapa.
- `Editar tarefa`: abre edição da tarefa.

---

## Tela 07 — Novo Departamento/Etapa / Editar Departamento/Etapa

**Rotas:**

- `/organiza/projetos/{projeto_id}/missoes/nova`
- `/organiza/projetos/{projeto_id}/missoes/{missao_id}/editar`

**Arquivo:** `templates/organiza/missao_form.html`  
**Tipo:** restrita

### Objetivo

Cadastrar ou editar uma etapa de trabalho dentro de um projeto.

### Campos

- Nome
- Observação

### Botões

- `Salvar`: grava a etapa.
- `Voltar`: retorna para a tela anterior.

### Observação de produto

No código, esta entidade ainda se chama `Missao`.  
Na tela, ela aparece como `Departamento / Etapa`.

A linguagem visível para o usuário deve permanecer como `Departamento / Etapa`.

---

## Tela 08 — Nova Tarefa / Editar Tarefa

**Rotas:**

- `/organiza/tarefas/nova`
- `/organiza/tarefas/{tarefa_id}/editar`

**Arquivo:** `templates/organiza/tarefa_form.html`  
**Tipo:** restrita

### Objetivo

Cadastrar ou editar tarefas do Organiza.

### Campos

- Projeto
- Departamento/Etapa
- Tarefa
- Responsável
- Status
- Tipo
- Dias da semana
- Dependência
- Descrição opcional

### Botões e ações

- `Salvar tarefa`: grava a tarefa.
- `Voltar`: retorna para a tela anterior.
- `Criar tarefa de dependência`: cria uma nova tarefa relacionada.

---

# 4. Usuários e Responsáveis

O sistema trabalha com três responsáveis fixos:

- Junior
- Debora
- Luiz

Esses nomes aparecem nos filtros e no cadastro de tarefas.

---

# 5. Status das Tarefas

O Organiza possui cinco status oficiais:

- Aberta
- Em andamento
- Aguardando
- Pronta
- Entregue

Esses status aparecem no painel, nos filtros e no formulário de tarefa.

---

# 6. Regras de Negócio Identificadas

## 6.1 Acesso

- O Organiza exige login.
- Sem login, o usuário é direcionado para a tela de entrada.

## 6.2 Painel

- O painel mostra as tarefas do responsável selecionado.
- Se nenhum responsável for selecionado, usa o usuário logado.
- O painel calcula quantidade de tarefas por status.
- O painel calcula tarefas de hoje por responsável.

## 6.3 Tarefas

- Toda tarefa pertence a um projeto.
- Toda tarefa pertence a um departamento/etapa.
- Toda tarefa possui responsável.
- Toda tarefa possui status.
- Uma tarefa pode ser única ou recorrente.
- Se for recorrente, pode ter dias da semana.
- Uma tarefa pode depender de outra tarefa do mesmo projeto.
- O status pode ser alterado diretamente por rota própria.

## 6.4 Projetos

- Um projeto pode ter vários departamentos/etapas.
- Um projeto pode ter várias tarefas.
- O progresso do projeto é calculado a partir das tarefas.

## 6.5 Departamentos / Etapas

- Um departamento/etapa pertence a um projeto.
- Um departamento/etapa pode ter várias tarefas.

---

# 7. Banco de Dados

O banco atual é SQLite.

**Arquivo:** `organiza.db`

## Tabelas

### usuarios

Guarda os usuários autorizados.

Campos principais:

- id
- nome
- senha_hash
- criado_em

### projetos

Guarda os projetos.

Campos principais:

- id
- nome
- observacao
- criado_em

### missoes

Guarda os departamentos/etapas.

Campos principais:

- id
- projeto_id
- nome
- observacao
- criado_em

### tarefas

Guarda as tarefas.

Campos principais:

- id
- projeto_id
- missao_id
- descricao
- responsavel
- status
- tipo
- dias_semana
- dependencia_id
- observacao
- criado_em

## Relações

```text
Projeto
│
├── Departamentos / Etapas
│   └── Tarefas
│
└── Tarefas

Tarefa
└── Pode depender de outra tarefa
```

---

# 8. Identidade Visual Encontrada

## Fontes

A fonte principal encontrada no CSS é:

- Inter
- Segoe UI
- Arial
- sans-serif

## Cores principais encontradas

As cores mais importantes identificadas foram:

- Azul forte: `#0066ff`
- Azul claro: `#00aeff`
- Verde HUMIAT: `#00a859`
- Verde destaque: `#00d26a`
- Fundo escuro: `#01050b`
- Fundo azul escuro: `#00142a`
- Texto claro: `#fff`
- Texto suave: `#a8bdd3`
- Azul muito escuro: `#02070f`

## Arquivos visuais

A pasta `static/img` possui:

- logo HUMIAT;
- favicon;
- ícones de plataformas;
- imagem hero desktop;
- imagem hero mobile;
- ícone do WhatsApp.

---

# 9. Componentes Identificados

## Site Institucional

- cabeçalho;
- menu;
- seção hero;
- cards de plataformas;
- blocos de conteúdo;
- botão de WhatsApp;
- rodapé.

## Organiza

- barra superior;
- cards de resumo;
- cards de status;
- lista de tarefas;
- cards de projeto;
- formulários;
- campos de texto;
- campos de seleção;
- botão principal;
- botão voltar;
- links de edição.

---

# 10. Rotas do Sistema

## Públicas

- `GET /`
- `GET /saude`
- `GET /area-restrita/login`
- `POST /area-restrita/login`

## Restritas

- `GET /area-restrita/sair`
- `GET /organiza`
- `GET /organiza/detalhes/{tipo}/{valor}`
- `GET /organiza/projetos/novo`
- `POST /organiza/projetos/novo`
- `GET /organiza/projetos/{projeto_id}/editar`
- `POST /organiza/projetos/{projeto_id}/editar`
- `GET /organiza/projetos/{projeto_id}`
- `GET /organiza/projetos/{projeto_id}/missoes/nova`
- `POST /organiza/projetos/{projeto_id}/missoes/nova`
- `GET /organiza/projetos/{projeto_id}/missoes/{missao_id}/editar`
- `POST /organiza/projetos/{projeto_id}/missoes/{missao_id}/editar`
- `GET /organiza/tarefas/nova`
- `POST /organiza/tarefas/nova`
- `GET /organiza/tarefas/{tarefa_id}/editar`
- `POST /organiza/tarefas/{tarefa_id}/editar`
- `POST /organiza/tarefas/{tarefa_id}/status`

---

# 11. Conclusão do Inventário

O projeto atual não é uma bagunça sem forma.

Ele já possui:

- produto institucional;
- sistema interno;
- login;
- painel;
- cadastro de projetos;
- cadastro de departamentos/etapas;
- cadastro de tarefas;
- responsáveis;
- status;
- banco de dados;
- identidade visual iniciada;
- documentação parcial.

O que faltava era transformar tudo isso em uma fonte oficial de conhecimento.

Este documento passa a ser o primeiro inventário organizado da HUMIAT V1.
