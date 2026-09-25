# Organiza 1.1.50 — filtros de Vendas e máquina de referência

- Corrige a grade dos filtros de Vendas para impedir sobreposição entre selects, inclusive Campanha e Envio da campanha.
- Campos passam a respeitar 100% da largura disponível e a grade se reorganiza conforme a tela.
- Em campanhas de atualização, clientes com mais de uma máquina ativa passam a usar somente a máquina mais antiga como referência.
- A máquina mais antiga é definida prioritariamente pela data da compra; na ausência dela, pela previsão de entrega e depois pela ordem histórica do cadastro.
- Se a máquina mais antiga estiver sem pacote, a atualização começa no primeiro pacote disponível e segue até o pacote-alvo.
