# HUMIAT Organiza 1.1.68

Simplificação da consulta de estoque e ajuste da regra de manutenção.

- Remove o histórico/movimentações recentes da tela principal de Estoque.
- A tela principal fica focada somente na posição atual: físico, vendas a fazer, manutenções aprovadas, disponível, mínimo e compra.
- Manutenções sem orçamento aprovado não reservam nem baixam estoque.
- Quando a manutenção é aprovada, somente os itens aprovados passam a reservar estoque.
- Ao encerrar uma manutenção nova já reservada, a reserva vira saída física.
- Migração única remove reservas antigas de manutenções ainda não aprovadas e recria apenas as reservas válidas.
- A consulta de Movimentações por período passa a ter duas visões:
  - Movimentações agrupadas por cliente: uma linha por Venda/Manutenção, sem listar peça por peça.
  - Movimentações por item: resumo consolidado por Item/Cor com entradas, saídas, reservas e movimento líquido.
- Exportação CSV passa a exportar o resumo por item.
