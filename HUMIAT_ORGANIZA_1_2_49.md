# HUMIAT Organiza 1.2.49

## Estoque — correção direta de movimentos locais

- Movimentos com origem `CONTAGEM` podem ser editados ou excluídos diretamente em **Movimentação completa**.
- Movimentos com origem `MANUAL` também podem ser editados ou excluídos diretamente.
- A edição permite corrigir Entrada/Saída, quantidade e observação.
- Excluir remove somente aquele lançamento local; o saldo passa a ser o resultado do histórico restante.
- Venda, Manutenção e Compra continuam bloqueadas para edição no extrato e exibem **Corrigir na origem**.
- O extrato não cria nova contagem nem reconta o estoque ao editar/excluir um movimento local.

## Regra da contagem

- A planilha de contagem continua gerando apenas a diferença como Entrada ou Saída.
- Após salvar, a planilha permanece zerada para uma nova contagem.
- Se uma contagem estiver errada, corrige-se o próprio ajuste gerado (por exemplo, Entrada 3 para Entrada 2), sem gerar uma nova contagem.
