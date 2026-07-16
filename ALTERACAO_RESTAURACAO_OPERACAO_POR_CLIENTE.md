# Restauração da Operação por Cliente

## Objetivo
Restaurar o fluxo operacional agrupado por cliente, permitindo trabalhar vários equipamentos em uma única ação.

## Alterações
- Contadores da Central Operacional passam a representar grupos de clientes.
- Etapa de recebimento agrupada por cliente, com seleção de vários equipamentos e confirmação única.
- Etapa de aprovação agrupada por cliente, com seleção conjunta e envio/reenvio único pelo WhatsApp.
- Execução e pausados exibidos agrupados por cliente.
- Equipamentos continuam acessíveis individualmente quando necessário.
- As filas continuam usando a regra operacional exclusiva para impedir equipamentos de aparecerem antes da etapa correta.

## Fluxo esperado
1. Abrir o cliente na etapa.
2. Marcar todos ou apenas os equipamentos desejados.
3. Executar uma única ação para o grupo.
