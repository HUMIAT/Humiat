# HUMIAT / Organiza 1.2.39

## Nova venda sem duplicar cliente

- Corrigida a localização do cliente pelo WhatsApp na abertura de **Nova venda**.
- O número agora é normalizado antes da busca, inclusive quando for informado com máscara ou com o DDI `55`.
- Cadastros legados também são comparados de forma normalizada, evitando criar um segundo cliente para o mesmo WhatsApp.
- A validação de duplicidade ao criar ou editar clientes passou a usar a mesma regra normalizada.

## Adicionar cliente dentro do fluxo de venda

- Incluído o botão **+ Adicionar cliente** ao lado de **Cliente existente** na tela de Nova venda.
- Ao cadastrar por esse botão, o Organiza volta automaticamente para **Nova venda** com o cliente recém-criado já selecionado.
- O botão Voltar e o texto de confirmação do cadastro se adaptam quando o acesso veio da venda.
