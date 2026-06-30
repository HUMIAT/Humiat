# ORGANIZA 5.1 - Arquivos alterados

Substitua somente estes arquivos no projeto atual:

1. `app.py`
2. `templates/organiza/base.html`
3. `templates/organiza/tarefa_form.html`
4. `templates/organiza/usuario_form.html`
5. `templates/organiza/painel.html`
6. `static/css/organiza.css`

Adicione estes arquivos novos:

1. `templates/organiza/chamados.html`
2. `templates/organiza/chamado_form.html`

## Banco de dados

Não precisa substituir o arquivo `organiza.db`.

O próprio `app.py`, ao iniciar, cria as novas tabelas e colunas:

- `chamados`
- `externos`
- `usuarios.departamentos`

Também troca tarefas antigas com status `Aberta` para `Pendente` e `Aguardando` para `Aguardando Cliente`.

## Fluxo implantado

- Tarefa com status `Aguardando Compras`, `Aguardando Financeiro` ou `Aguardando Externo` cria automaticamente 1 chamado para o departamento.
- O chamado pertence ao departamento, não a uma pessoa.
- Usuários marcados no departamento enxergam o mesmo chamado.
- Ao concluir o chamado, a tarefa original volta para `Pendente`.
- Compras só conclui depois de informar a data de recebimento.
- Cliente agora pode ser pesquisado digitando nome ou telefone.
- Externo pode ser pesquisado ou cadastrado dentro do chamado externo.
