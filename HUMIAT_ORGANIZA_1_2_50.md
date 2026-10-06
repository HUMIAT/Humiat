# HUMIAT Organiza 1.2.50

## Data correta no razão de estoque

- Saídas de **Venda** usam a **Data da compra** cadastrada na venda.
- Saídas de **Manutenção** usam a data de **recebimento/entrada da manutenção**.
- Recalcular ou editar a origem depois não desloca mais o movimento para a data de hoje.
- Migração única corrige a data dos movimentos automáticos de venda e manutenção já existentes, sem alterar quantidade, tipo ou saldo.
- Entradas manuais e ajustes de contagem continuam usando a data em que foram lançados/corrigidos localmente.
- Compra de material em estoque continua entrando na data em que a chegada é confirmada, pois é quando o material passa a existir fisicamente no estoque.
