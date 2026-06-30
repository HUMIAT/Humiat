# ORGANIZA V4.1 - Mobile, Projetos/Etapas e Exclusão de Tarefas

## Correções aplicadas

1. Menu mobile passou a ser global em todas as telas internas.
2. Projetos e Etapas foram unificados em uma única tela: `/organiza/projetos`.
3. O menu lateral agora mostra `Projetos / Etapas`, evitando redundância.
4. Botões administrativos respeitam permissões do usuário:
   - Criar projeto
   - Editar projeto
   - Criar etapa
   - Editar etapa
   - Criar usuário
5. Usuário sem permissão no botão central mobile recebe tela de acesso negado.
6. Adicionada exclusão de tarefa com regra:
   - Admin pode excluir qualquer tarefa.
   - Usuário comum só pode excluir tarefa sob sua responsabilidade.
7. Tela de projeto recebeu botão Excluir nas tarefas permitidas.

## Arquivos principais alterados

- `app.py`
- `templates/organiza/base.html`
- `templates/organiza/painel.html`
- `templates/organiza/projetos.html`
- `templates/organiza/etapas.html`
- `templates/organiza/projeto.html`
- `templates/organiza/acesso_negado.html`
- `static/css/organiza.css`

## Observação

A tela `Etapas` foi mantida apenas como redirecionamento visual para a nova tela unificada, para não quebrar links antigos.
