# HUMIAT / Organiza 1.2.20

## HUMIAT Design System v1

- Cria uma camada visual mestre compartilhável entre Organiza, Humiat ID, Connect, SolVoz e LokaFest.
- Padroniza tipografia, alturas de controles, botões, cartões, raios, espaçamentos e estados semânticos.
- O Organiza passa a consumir as variáveis `--h-*` do Design System, sem depender de uma cor fixa espalhada nas telas.
- A identidade da empresa altera somente os pontos de marca (`primary`, `secondary`, `accent`); fundo e superfícies permanecem neutros e padronizados.
- Sem identidade SolVoz disponível, o tema HUMIAT é usado automaticamente.

## Paleta SolVoz como fonte da empresa

- A rotina manual **Atualizar empresas do SolVoz** também sincroniza e armazena localmente a paleta visual de cada Empresa SolVoz.
- O Organiza usa o cache local da paleta; não consulta o SolVoz durante a abertura/renderização das páginas.
- A empresa do Organiza é escolhida por `ORGANIZA_THEME_EMPRESA_SLUG` (fallback `karaokerj`).
- Se a paleta da empresa não estiver no cache local, o tema HUMIAT assume sem bloquear o sistema.

## E-mails HUMIAT

- Primeiro acesso, reenvio/refazer acesso, migração e recuperação de senha passam a incluir um bloco padronizado com os endereços dos sistemas.
- Endereços exibidos: Central Humiat ID, Organiza, Contratos / Connect, LokaFest e SolVoz.
- Os links usam as URLs configuradas no ambiente; não ficam presos a endereços duplicados no template.
- O transporte continua centralizado no Resend HUMIAT, preservando a cópia oculta administrativa configurada.
