# Humiat / Organiza 8.7 — Administração SolVoz integrada

## Administrador Humiat

A seção **SolVoz + Organiza > Máquinas do Organiza** passa a mostrar,
para cada máquina, os dados já entregues pelo SolVoz:

- status online/offline;
- fila real;
- pedidos pendentes;
- último pedido;
- nome do cantor no último pedido;
- lista de pedidos aguardando com código + nome;
- total de notas recebidas;
- Top 10 do ranking;
- últimas notas enviadas pelo KRJ Monitor.

## Arquitetura mantida

O administrador continua no Humiat.

Não foi criado acesso administrativo direto no SolVoz.

Fluxo:

Humiat Admin
→ API privada do SolVoz
→ `/_sv/api/humiat/empresa/{slug}`
→ dados de máquinas, fila, cantor e ranking.

Requer o SolVoz 2.4.23 (ou superior), que já devolve esses campos.
