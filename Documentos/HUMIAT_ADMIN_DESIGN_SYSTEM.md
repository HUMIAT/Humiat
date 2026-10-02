# HUMIAT Admin Design System

Padrão visual administrativo adotado inicialmente no Organiza e preparado para futura aplicação em Connect, SolVoz, Humiat ID e demais produtos administrativos.

## Princípio
O modelo escolhido é referência de linguagem visual, não de regra de negócio. Cada sistema mantém seus fluxos, conteúdo e identidade. A estrutura de componentes é compartilhada.

## Tokens neutros
- Fundo da aplicação: `#F0F4F8`
- Superfície/card: `#FFFFFF`
- Hover neutro: `#F8FAFC`
- Fundo secundário: `#F1F5F9`
- Texto principal: `#1E293B`
- Texto secundário: `#64748B`
- Texto discreto: `#94A3B8`
- Borda: `#E4E8EF`
- Borda suave: `#EEF1F5`

## Identidade
A cor primária não fica fixa no componente. Ela vem de `--h-primary`, definida pelo HUMIAT/empresa. Os derivados são `--h-primary-hover`, `--h-primary-soft` e `--h-primary-border`.

## Componentes
- Sidebar: 248 px em desktop, branca, grupos com rótulo discreto e item ativo em fundo suave + faixa primária.
- Botão padrão: raio 8 px, altura mínima 36 px, texto 13 px / 600.
- Campo/Select: raio 8 px, altura mínima 38 px, foco com borda primária e anel suave.
- Card: raio 14 px, borda neutra e sombra curta.
- Tabela: cabeçalho de 10 px em caixa alta, linhas de 13 px, hover neutro.
- Ação por ícone: 34 x 34 px.
- Status: formato pill, cores semânticas e texto compacto.

## Semântica
- Sucesso: `#10B981`
- Alerta: `#F59E0B`
- Erro: `#EF4444`
- Informação: `#0EA5E9`

## Regra para os próximos sistemas
Ao aplicar em outro produto, não copiar CSS de tela. Reutilizar tokens, dimensões e estados deste padrão e mapear as classes existentes do produto para os mesmos componentes visuais.
