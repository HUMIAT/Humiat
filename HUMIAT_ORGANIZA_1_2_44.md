# HUMIAT Organiza 1.2.44 — regra única de estoque e extrato

## Regra única
- Venda com **Descontar materiais desta venda do estoque** marcada gera saída física imediatamente, inclusive em Solicitar gabinete, Montagem e Pronto para entrega.
- A venda deixa de usar reserva como fonte de saldo. Edição da composição atualiza a mesma saída, sem duplicar.
- Venda marcada como **não descontar** remove sua saída automática.
- Manutenção mantém a regra operacional: orçamento aprovado + **Descontar do estoque** gera saída física; não aprovada não baixa; **Não descontar** remove a saída automática.
- Posição, compras, movimentações e CSV passam a usar `estoque_movimentos` como única fonte de verdade.

## Posição de estoque
- **Físico** já é o saldo real após entradas e saídas.
- **Saídas Vendas** mostra quanto foi baixado por vendas.
- **Saídas Manut.** mostra quanto foi baixado por manutenções.
- **Disponível = Físico**; vendas/manutenções não são subtraídas uma segunda vez.
- **Comprar = max(Mínimo - Físico, 0)**.

## Extrato
- A antiga seção “Movimentações por item” foi substituída por **Extrato por movimento**.
- Com data inicial, mostra **Saldo inicial**, depois cada Entrada/Saída em ordem cronológica e o **Saldo** após cada lançamento.
- Venda e manutenção aparecem com origem e cliente.
- CSV segue o mesmo formato de extrato.

## Correção de dados existentes
- Migração 1.2.44 converte reservas de venda existentes em saídas físicas preservando a data da reserva quando possível.
- Ressincroniza vendas do fluxo comercial e manutenções para corrigir registros que não tinham sido refletidos.
- Remove reservas de estoque legadas após a conversão.
