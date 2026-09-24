# HUMIAT Organiza 1.1.37

- Lista de Clientes de Aluguel agora grava Último mês de aluguel e Integração (Planilha/Connect).
- Registros importados ficam como Planilha.
- Connect passa a ser a fonte prioritária e atualiza nome, telefone e último aluguel sem duplicar.
- Vinculação do Connect usa connect_cliente_id e telefone normalizado.
- Reimportar planilha não sobrescreve registros já atualizados pelo Connect.
- Lista de aluguel permite filtrar por mês e por integração.
- Campanhas de aluguel podem segmentar pelo último mês de aluguel.
- Novo endpoint privado: POST /api/integracoes/connect/clientes-aluguel.
