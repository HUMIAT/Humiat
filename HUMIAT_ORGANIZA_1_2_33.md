# HUMIAT / Organiza 1.2.33

## Manutenção e estoque

- Orçamento de manutenção ainda não aprovado passa a reservar os materiais.
- Aprovação pelo cliente ou aprovação manual transforma os itens aprovados em saída física imediatamente; não espera o encerramento da manutenção.
- A reserva é removida no mesmo momento da aprovação.
- Cancelamentos e encerramentos sem aprovação liberam reservas.
- A sincronização de estoque é idempotente: corrigir quantidade, cor ou modalidade de aprovação atualiza a mesma movimentação e não duplica baixa.
- Migração única na inicialização recalcula manutenções antigas, corrigindo OS aprovadas que ficaram apenas reservadas, inclusive o cenário relatado do cliente Jonathan.
- Na manutenção aprovada há ação **Recalcular estoque desta manutenção** para correção pontual/manual, caso necessário.
