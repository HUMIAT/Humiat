# HUMIAT / Organiza 1.2.35

## Estoque: regra única para vendas e manutenções

- Reserva de estoque existe somente para venda.
- Manutenção pendente/não aprovada não reserva nem aparece na movimentação.
- Manutenção aprovada baixa os materiais aprovados diretamente do físico.
- Venda e manutenção possuem opção **Não descontar do estoque**.
- Ao marcar **Não descontar**, reservas/saídas automáticas da origem são excluídas da movimentação; não é criada linha de estorno.
- A posição do estoque mostra apenas Físico, Vendas reservadas e Disponível.
- Migração única remove reservas antigas de manutenção e ressincroniza as manutenções existentes.
