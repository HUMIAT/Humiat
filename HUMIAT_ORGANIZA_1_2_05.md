# HUMIAT / ORGANIZA 1.2.05

- Elimina N+1 da tela de Vendas: composição, opcionais e regras de modelo são carregados em lote.
- Campanhas usadas em Vendas/Atualizações não carregam imagem/BLOB quando a tela só precisa de texto.
- Atualizações deixa de consultar Google/SolVoz/links técnicos em toda abertura; configurações técnicas são carregadas somente quando solicitadas.
- Elimina N+1 de agendamentos na tela Atualizações, usando uma consulta em lote.
- Sessão Humiat deixa de gravar `ultimo_acesso` durante navegação.
- Adiciona padrão global de ação compacta por ícone e aplica na área de Atualizações.
- Área de Google e links de atualização fica recolhida/fora do fluxo principal.
