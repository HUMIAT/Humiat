# HUMIAT / Organiza 1.2.37

## Estoque: vendas + manutenções no cálculo de compras

- A **Posição do estoque** passa a consultar também as manutenções aprovadas que ainda estão abertas.
- A tabela agora separa **Vendas** e **Manutenções**, permitindo conferir de onde vem a necessidade.
- A coluna **Disponível** usa: `Físico - Vendas - Manutenções`.
- O **Relatório de compras** usa a mesma base da posição do estoque.
- A regra de compra passa a funcionar também quando o estoque mínimo é zero: `Comprar = máximo(Mínimo - Disponível, 0)`.
- Exemplo validado: físico 1, venda 1, manutenções 2 e mínimo 0 → disponível -2 → **comprar 2**.
- Manutenções encerradas/canceladas não permanecem como necessidade de compra.
- A regra de movimentação não mudou: reserva continua exclusiva de venda; manutenção aprovada continua gerando saída física.
