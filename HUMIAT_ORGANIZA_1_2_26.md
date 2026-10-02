# HUMIAT / Organiza 1.2.26

## Itens — fornecedor por item
- Novo cadastro de Fornecedores no Organiza.
- Cada Item possui seu próprio fornecedor.
- Não existe vínculo permanente entre Categoria e Fornecedor.
- A lista inicial criada nesta versão é:
  - Marcelo - KN
  - Walter - Canaã
  - Cleyton - Gabinetes
  - Mercado livre
  - Nelio - Entronix
  - Boa dica
  - Aliexpress
  - Magazine Luiza
  - Shoppe
  - Frankil Microfones
- Somente na implantação inicial, itens sem fornecedor recebem sugestão conforme a categoria atual:
  - Espelhos → Marcelo - KN
  - Fliperama → Mercado livre
  - Gabinetes → Walter - Canaã
  - Geral → Mercado livre
  - Info e Eletrônicos → Boa dica
  - Som → Mercado livre
  - Botões / Botões e LEDs → Mercado livre
- Depois dessa primeira carga, mudar a categoria não muda o fornecedor automaticamente.
- Novos itens permitem escolher o fornecedor manualmente.

## Compras agrupadas por fornecedor
- O relatório de reposição passa a ser ordenado e separado por Fornecedor.
- A lista de compras aguardando chegada também fica agrupada por fornecedor.
- O fornecedor é gravado no pedido no momento da compra para preservar o histórico.

## Estoque mínimo em Itens
- Estoque mínimo saiu da tela de Contagem Física.
- Para itens sem cor, o mínimo é editado diretamente na planilha de Itens.
- Para itens controlados por cor, o botão `Por cor` abre a configuração de mínimos por cor dentro da própria tela de Itens.
- A contagem física não grava nem altera mais o estoque mínimo.
- O CSV da contagem também deixa de misturar estoque mínimo com a contagem física.
