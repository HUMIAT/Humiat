# Alteração — Relatório de vendas e sincronização dos recebimentos

## Objetivo

Garantir que os valores exibidos em **Vendas de equipamentos** e no novo **Relatório de vendas** usem a mesma fonte de dados e respeitem os filtros selecionados.

## Decisões aprovadas

- O filtro **Status** continua aceitando múltiplos status.
- O botão **Relatório** fica no topo da tela de vendas, ao lado de **+ Nova venda**.
- O relatório utiliza os filtros atuais da tela: busca/equipamento, pagamento, valor, status e ordenação.
- O valor **Recebido** é calculado pelos registros de `PagamentoVenda`.
- Vendas antigas ou recém-cadastradas com valor no campo legado `pago`, mas ainda sem registro em `PagamentoVenda`, são sincronizadas automaticamente como pagamento histórico.
- Pagamentos históricos sincronizados são marcados para não serem enviados ao Connect.

## Relatório

O relatório apresenta, por equipamento:

- Cliente;
- Equipamento e código técnico;
- Status;
- Data da compra;
- Data prevista de entrega;
- Valor total;
- Valor recebido;
- Valor que falta receber.

Também apresenta os totais gerais das vendas filtradas e permite **Imprimir / salvar PDF** pelo navegador.

## Arquivos alterados

- `app.py`
- `templates/organiza/vendas.html`
- `templates/organiza/vendas_relatorio.html` (novo)
- `templates/organiza/base.html`

## Manutenção futura

- Regras de cálculo, sincronização e filtros: função `_vendas_filtradas` em `app.py`.
- Layout do botão e filtros: `templates/organiza/vendas.html`.
- Layout do relatório: `templates/organiza/vendas_relatorio.html`.
