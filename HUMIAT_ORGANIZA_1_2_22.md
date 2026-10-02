# HUMIAT / Organiza 1.2.22

## Organiza — novo padrão administrativo
- Aplica ao Organiza a linguagem visual administrativa escolhida a partir do Adminator, adaptada à realidade atual do sistema.
- Mantém intactos banco, rotas, regras de negócio, integrações e fluxos existentes.
- Sidebar passa a usar fundo claro, grupos compactos, seleção com faixa da cor da empresa e hierarquia visual mais limpa.
- Padroniza cartões, métricas, botões, botões secundários, ações por ícone, campos, selects, textareas, tabelas, filtros, chips, alertas e diálogos.
- Mantém a cor principal dinâmica por empresa através das variáveis do HUMIAT Design System.
- Preserva os ícones reais e a identidade já implementada no HUMIAT 1.2.21.
- Mantém a densidade necessária às telas operacionais do Organiza, sem copiar espaçamentos excessivos de um dashboard demonstrativo.
- Melhora foco, hover, disabled, bordas e estados semânticos de sucesso, alerta, erro e informação.
- Preserva o comportamento responsivo e atualiza a aparência da barra e navegação mobile.

## Arquitetura visual
- Nova camada: `static/css/organiza-adminator.css`.
- A camada é carregada por último em `templates/organiza/base.html`, permitindo padronizar telas antigas sem reescrever templates nem CSS histórico agora.
- Essa estratégia facilita migração gradual: os estilos legados podem ser eliminados tela a tela depois, sem risco para a operação.
