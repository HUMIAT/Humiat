# HUMIAT Organiza 1.1.76

## Cadastro de Itens em planilha
- Categoria agora é um `select` real, abrindo todas as categorias ao clicar.
- Campo **Nova categoria** adiciona a categoria à lista da planilha sem recarregar a página.
- Nome, categoria, custo, preço, controle de estoque e status são editados diretamente na tabela.
- Removidos os botões individuais Editar / Ativar / Inativar da listagem.
- Um único **Salvar alterações** grava todas as linhas exibidas.

## Compras do estoque em lote
- O relatório de reposição ganhou uma caixa de seleção por linha.
- A quantidade sugerida de compra pode ser alterada para mais ou para menos na própria planilha.
- Os itens marcados aparecem imediatamente na seção **Compra selecionada** acima.
- O usuário informa uma única data prevista de chegada e salva todo o lote de uma vez.
- O valor estimado de cada linha é calculado pelo custo atual do Item x quantidade selecionada.
- Cada item salvo continua aparecendo em **Compras aguardando chegada** e só aumenta o estoque quando a chegada é confirmada.
