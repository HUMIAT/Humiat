# ORGANIZA V4 - Usuários, permissões, projetos e etapas

## Entregue nesta versão

- Cadastro de usuários com telefone/WhatsApp obrigatório.
- Campos de usuário: nome, telefone, email, cargo, senha, ativo.
- Permissões por usuário:
  - Administrador
  - Criar tarefa
  - Criar projeto
  - Criar usuário
  - Criar etapa
- Menu administrativo de Usuários visível somente para admin/permissão.
- Menu de Projetos para visualizar projetos e progresso.
- Tela de Etapas por projeto.
- Tela para escolher o projeto antes de criar etapa.
- Restrição de rotas administrativas no backend.
- Usuários ativos do banco passam a aparecer como responsáveis das tarefas.

## Senha inicial

A senha padrão continua vindo da variável ORGANIZA_SENHA_PADRAO.
Se não existir, usa: humiat123

## Primeiro administrador

Na inicialização, o usuário Junior é marcado como administrador para evitar travar o acesso ao sistema.

## Observação importante

O banco antigo é atualizado automaticamente no startup com as novas colunas da tabela usuarios.
