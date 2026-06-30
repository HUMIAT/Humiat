# ORGANIZA 5.2 — Painel de Fluxo

Substitua apenas estes arquivos no seu projeto:

1. `app.py`
2. `templates/organiza/painel.html`
3. `static/css/organiza.css`

## O que foi implantado

- Painel deixou de usar dados fixos nos cards laterais.
- Painel agora mostra dados reais de tarefas e chamados.
- Card **Pendente** mostra tarefas que voltaram para o responsável.
- Card **Chamados dos seus setores** mostra chamados abertos para os departamentos do usuário.
- Blocos de **Compras**, **Financeiro** e **Externo** mostram quantidade de chamados abertos.
- Bloco **Atrasados** usa chamados de compras com entrega vencida e externos com retorno vencido.
- Ao concluir chamado, a regra já existente permanece: a tarefa volta para **Pendente**.
- Compras continua não permitindo concluir sem data de recebimento.

## Atenção

Essa versão depende da 5.1, porque usa as tabelas de `chamados`, `externos` e o campo `departamentos` do usuário.
