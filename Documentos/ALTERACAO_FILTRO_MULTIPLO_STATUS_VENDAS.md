# Filtro múltiplo de status em Vendas

## Objetivo
Permitir selecionar mais de um status simultaneamente no filtro da tela **Vendas de equipamentos**.

## Regra aprovada
- O filtro **Status** aceita zero, um ou vários status.
- Sem nenhum status selecionado, a listagem considera **Todos**.
- Com vários status selecionados, a venda é exibida quando seu status estiver em qualquer um dos status marcados.
- A seleção é preservada ao navegar entre as páginas da listagem.

## Interface
O campo de status passou a utilizar uma lista com caixas de seleção.
O resumo do campo mostra:
- `Todos`, quando nada estiver selecionado;
- o nome do status, quando houver apenas um;
- `N status selecionados`, quando houver mais de um.

## Arquivos alterados
- `app.py`
- `templates/organiza/vendas.html`

## Observação técnica
Os status selecionados são enviados pela URL repetindo o parâmetro `status`, por exemplo:

`?status=Montagem&status=Pronto+para+entrega`

O backend utiliza `request.query_params.getlist("status")` para receber todos os valores.
