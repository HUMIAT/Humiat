# HUMIAT Organiza 1.2.45

## Estoque — parâmetro explícito, status neutro

- Venda: somente `descontar_estoque` decide a saída física; status operacional não decide.
- Venda cancelada remove a saída automática da origem.
- Nova venda passa a mostrar a regra de estoque já na criação, evitando baixa aparecer apenas quando muda para Montagem.
- Manutenção: somente `descontar_estoque` + itens aprovados decide a saída; etapa/status operacional não ativa a baixa.
- Manutenção cancelada remove a saída imediatamente, inclusive cancelamento pelo link público/agenda.
- Formulários auxiliares não alteram mais o status do equipamento para `Ativo` quando o campo status não foi enviado.
- Migração 1.2.45 reconcilia operações recentes e origens já movimentadas usando a mesma regra, respeitando a última contagem física.
