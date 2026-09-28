# HUMIAT Organiza 1.1.73

## Estoque: itens não físicos, valor da contagem e formulário compacto

- Itens da categoria **SISTEMA** deixam de controlar estoque.
- Itens da categoria **MANUTENÇÃO** deixam de controlar estoque.
- O item **ATUALIZAÇÃO** também fica fora do estoque, mesmo que esteja em categoria antiga.
- Esses itens continuam disponíveis para venda/orçamento/manutenção, mas não entram em saldo, reserva, contagem, mínimo, compras ou histórico de estoque.
- Cadastro de Itens passa a mostrar a coluna **ESTOQUE: Sim/Não**.
- Formulários de Novo Item e Editar Item reorganizados em uma linha compacta: Item, Categoria, Custo, Preço e Salvar.
- A Planilha de Contagem passa a mostrar **Custo Unitário** e **Valor Contado**.
- O valor contado é calculado por quantidade física × custo atual do Item.
- O topo da contagem mostra o **Valor total do estoque já contado**, atualizado também enquanto a contagem é digitada.
- CSV da contagem passa a exportar custo unitário e valor contado.

Versão: 1.1.73
