# HUMIAT / Organiza 1.2.19

- Empresas SolVoz: ação global **Copiar responsáveis dos equipamentos** para empresas sem responsável e com um único cliente detectado.
- Empresas sem responsável exibem aviso explícito de vínculo obrigatório no ADM.
- Classificação de área do LokaFest passa a ignorar acentos no município/bairro (ex.: Nova Iguaçu -> Baixada Fluminense).
- Ao concluir onboarding/vínculo LokaFest, o cache da fila de pendências é invalidado para não manter usuário já regular como pendente.
- Pendências Humiat ID identifica cliente com Empresa SolVoz pelos equipamentos mas sem responsável e bloqueia aprovação simples, orientando corrigir o responsável no ADM.
