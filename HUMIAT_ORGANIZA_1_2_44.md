# HUMIAT Organiza 1.2.44

## Estoque — razão único, sem reservas

- Venda com **Descontar estoque** marcado gera **SAÍDA física imediatamente** dos materiais usados na máquina.
- Manutenção com **Descontar do estoque** marcada gera **SAÍDA física** dos materiais aprovados, exceto quando cancelada.
- Reservas de venda/manutenção deixam de participar do estoque e são removidas na migração 1.2.44.
- Posição do estoque passa a usar uma única conta: **Entradas − Saídas = Saldo físico/Disponível**.
- Colunas de Venda e Manutenção passam a ser informativas e mostram as saídas já registradas por origem; não são abatidas novamente.
- Relatório de compras usa apenas o saldo físico atual e o estoque mínimo, evitando desconto duplo.
- Movimentação completa vira um **extrato cronológico**, estilo conta bancária, com saldo antes e saldo após cada lançamento.
- CSV de movimentação segue o mesmo extrato e a mesma regra.
- Migração recalcula vendas e manutenções existentes para corrigir operações que estavam como reserva ou fora dos relatórios.
