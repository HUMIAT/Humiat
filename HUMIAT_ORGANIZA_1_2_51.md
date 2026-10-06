# HUMIAT Organiza 1.2.51

## Compras aguardando chegada

- Compra salva como **Aguardando** reduz imediatamente a quantidade ainda necessária no Relatório de reposição, sem aumentar o estoque físico.
- O relatório passa a mostrar a coluna **Em compra** para deixar visível o que já foi pedido e ainda não chegou.
- Enquanto estiver aguardando, a compra permite editar **quantidade** e **data prevista de chegada**.
- Alterar a quantidade recalcula o valor total mantendo o custo unitário do pedido.
- **Confirmar chegada** usa a quantidade atualmente informada e só nesse momento cria a ENTRADA física no estoque.
- Ao confirmar, a quantidade deixa de contar como “Em compra” e passa a compor o saldo físico, sem duplicar a necessidade de compra.
