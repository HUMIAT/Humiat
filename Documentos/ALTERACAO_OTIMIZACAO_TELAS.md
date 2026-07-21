# Otimização das telas Vendas, Agenda e Central Financeira

- Removida a consulta individual de pagamento para cada venda durante a migração de recebimentos antigos.
- A listagem de vendas agora busca no banco somente equipamentos que podem representar vendas.
- As opções de status são calculadas com o resultado já carregado, evitando nova leitura integral da tabela de equipamentos.
- A Central Financeira passou a carregar integrações e totais de pagamentos em lote.
- Relacionamentos de orçamento, itens e pagamentos são pré-carregados para eliminar consultas repetidas por lançamento.
- A Agenda pré-carrega os pagamentos dos orçamentos necessários para calcular a etapa operacional sem consultas por manutenção.
