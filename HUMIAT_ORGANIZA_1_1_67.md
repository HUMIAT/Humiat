# HUMIAT Organiza 1.1.67

Correção e auditoria da primeira implantação do estoque.

- Corrige a quebra visual das linhas da planilha de estoque causada por conflito da classe global `linha-item`.
- Cada item volta a ocupar uma única linha com todas as colunas alinhadas.
- Vendas a Fazer e Manutenções a Fazer ficam clicáveis para mostrar onde o item está reservado.
- A área de movimentos recentes passa a mostrar também as reservas atuais, com origem, cliente e status.
- Nova tela `Movimentação completa` para consultar estoque entre datas.
- Filtros por período, Item, tipo (Entrada/Saída/Reserva) e origem (Venda/Manutenção/Manual/Contagem/Estorno).
- Exportação CSV da movimentação filtrada.
- Lançamentos manuais deixam de ser apagados: o botão passa a criar estorno, preservando o histórico.
- As baixas experimentais anteriores à primeira implantação, removidas pela 1.1.66, continuam fora do saldo; a partir da implantação, entradas/saídas válidas e a origem operacional ficam consultáveis.
