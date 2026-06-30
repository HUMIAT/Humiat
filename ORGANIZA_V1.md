# Organiza — V1

## Objetivo

O Organiza é a área restrita da HUMIAT para lembrar quem está fazendo o quê, o que está pendente e qual é o próximo passo de cada projeto.

## Caminho no site

```text
Área Restrita
└── Login com usuário e senha
    └── Organiza
```

## Usuários iniciais

- Junior
- Debora
- Luiz

A senha inicial é definida pela variável de ambiente `ORGANIZA_SENHA_PADRAO`.

## Regras da V1

Cada tarefa pertence a:

```text
Projeto
└── Missão
    └── Tarefa
        └── Responsável
```

Status disponíveis:

- Aberta
- Em andamento
- Aguardando
- Concluída

Tipos de tarefa:

- Única
- Recorrente por dias da semana

Campos da tarefa:

- Projeto
- Missão
- Descrição
- Responsável
- Status
- Tipo
- Dias da semana, se for recorrente
- Dependência opcional
- Observação opcional

## Notificações

Na V1, as notificações são internas.

Quando o usuário entra no Organiza, o painel mostra as tarefas dele para hoje.

## Banco de dados

O sistema usa SQLAlchemy.

Para desenvolvimento local, se `DATABASE_URL` não existir, usa SQLite:

```text
sqlite:///./organiza.db
```

Para produção no Render Free, usar banco externo, por exemplo Supabase ou Neon, configurando:

```text
DATABASE_URL=postgresql://...
SECRET_KEY=uma-chave-secreta-forte
ORGANIZA_SENHA_PADRAO=senha-inicial-da-equipe
```

Depois do primeiro deploy, recomenda-se trocar as senhas diretamente no banco ou criar uma tela de troca de senha em uma próxima versão.

## Rotas principais

- `/area-restrita/login`
- `/area-restrita/sair`
- `/organiza`
- `/organiza/projetos/novo`
- `/organiza/projetos/{id}`
- `/organiza/projetos/{id}/missoes/nova`
- `/organiza/tarefas/nova`
