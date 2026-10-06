# HUMIAT Organiza 1.2.47

- Separa definitivamente **contagem física** de **recalcular estoque**.
- Recalcular não cria movimento `CONTAGEM` e não inicia nova contagem.
- No extrato, lançamentos de origem **Contagem física** passam a ter a ação **Excluir e recalcular**.
- Ao excluir uma contagem incorreta, somente aquele ajuste é removido e o saldo atual é recalculado pelas entradas e saídas restantes.
- Se houver uma contagem física anterior para o mesmo item/cor, o progresso da planilha volta para a última contagem válida.
- Nenhum movimento novo de contagem é criado durante essa correção.
