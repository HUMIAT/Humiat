# HUMIAT Organiza 1.1.75

## Itens / categorias
- Cadastro de itens em modo planilha para Categoria e Controlar estoque.
- Várias linhas podem ser alteradas e salvas uma única vez.
- É possível criar uma categoria nova digitando o nome diretamente na célula Categoria.
- Categorias padrão: Sistema, Manutenção, Info e Eletrônicos, Som, Cabos e Conectores, Botões e LEDs, Gabinetes, Espelhos, Fliperama e Geral.
- Migração: Informática -> Info e Eletrônicos; itens com GABINETE -> Gabinetes; ESPELHO -> Espelhos; FLIPERAMA -> Fliperama.

## Estoque
- Filtros numéricos mantidos horizontalmente em uma única linha: Campo, condição, Valor, Aplicar e Limpar.

## Compras de estoque
- Tela /organiza/estoque/compras agora reúne relatório de reposição e compras em andamento.
- Nova compra informa Item, Cor quando aplicável, Quantidade, Valor total, previsão de chegada e observação.
- O botão Comprar em uma linha do relatório preenche Item, Cor, Quantidade e Valor estimado.
- Compra registrada fica Aguardando e não altera o estoque físico.
- Confirmar chegada cria uma única entrada física de estoque e marca a compra como Recebida.
- Proteção idempotente impede que a mesma compra aumente o estoque duas vezes.
