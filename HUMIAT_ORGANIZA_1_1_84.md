# HUMIAT Organiza 1.1.84

- Separa o link simples de cadastro do novo link público da venda.
- Nova venda aberta pela ficha do cliente já nasce vinculada ao cliente, sem pedir os dados novamente.
- Nova venda permite montar opcionais e frete antes de gerar o link para o cliente.
- Adiciona prazo de produção configurável por equipamento comercial, com padrão de 20 dias corridos.
- A contagem de produção começa no dia seguinte à compra; se a data final cair em fim de semana ou feriado, a entrega é movida para o próximo dia útil.
- O link público da venda permite completar cadastro, conferir equipamento e ajustar somente opcionais habilitados.
- O equipamento principal não pode ser trocado pelo cliente.
- O total é recalculado quando opcionais mudam, preservando desconto, cupom e frete.
- Após existir qualquer pagamento, a configuração comercial fica congelada para o cliente.
- Antes de abrir a InfinitePay, o saldo é consultado novamente no servidor. Venda quitada não gera nova cobrança; venda parcial cobra somente o saldo.
- Primeiro pagamento registrado (manual ou InfinitePay) fixa a data da compra e calcula automaticamente a previsão de entrega.
- Mantém integralmente os fluxos manuais de venda e pagamento.
