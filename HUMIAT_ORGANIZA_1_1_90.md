# Organiza 1.1.90

- Adiciona em Vendas a opção **Excluir venda**.
- Adiciona **Excluir venda + cliente** para cadastros criados somente para vendas desistidas.
- A exclusão remove equipamento, link/token da venda, reservas de estoque, cores e cobranças InfinitePay ainda não pagas ligadas à venda.
- Bloqueia exclusão quando houver pagamento confirmado, saída definitiva de estoque, manutenção ou transferência.
- O cadastro do cliente só é excluído quando, após retirar a venda, não restarem equipamentos nem vínculos operacionais/fiscais relevantes.
