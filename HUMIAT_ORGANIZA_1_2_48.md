# HUMIAT Organiza 1.2.48

## Estoque — contagem física como ajuste de razão

- A planilha de contagem não redefine nem reinicia o estoque.
- Cada valor contado é comparado ao saldo atual do sistema.
- Se o físico contado for maior, é criado um movimento `ENTRADA` com origem `CONTAGEM` somente pela diferença.
- Se o físico contado for menor, é criado um movimento `SAIDA` com origem `CONTAGEM` somente pela diferença.
- Se for igual, nenhum movimento é criado.
- Depois de salvar, a planilha volta vazia para a próxima contagem; valores da contagem anterior não ficam preenchidos.

## Correção de movimentos

- Ajustes de `CONTAGEM` podem ser editados ou excluídos diretamente em **Movimentação completa**.
- A edição permite corrigir se o ajuste é Entrada ou Saída e a quantidade.
- Excluir um ajuste de contagem remove somente aquele lançamento; nenhum novo lançamento é criado.
- Venda, Manutenção e Compra exibem **Corrigir na origem** e não podem ser alteradas diretamente pelo extrato.
- O saldo continua sendo calculado como razão cronológico: entradas somam e saídas reduzem.

## Relatórios

- Entradas geradas por contagem continuam aparecendo em Entradas de estoque.
- Saídas geradas por contagem aparecem no extrato de Movimentação completa.
- A correção de ambos é feita no mesmo local: **Movimentação completa**.
