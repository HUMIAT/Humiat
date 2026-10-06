# HUMIAT Organiza 1.2.46

## Correção da posição do estoque

- A tela **Posição do estoque** agora usa exatamente o mesmo razão físico da tela **Movimentação completa**.
- **Saídas venda** e **Saídas manutenção** somam todas as saídas físicas registradas, sem corte oculto pela data da última contagem.
- O saldo físico considera exclusivamente movimentos `ENTRADA` e `SAIDA`; qualquer registro legado de outro tipo não altera o saldo.
- A contagem física continua sendo registrada como ajuste no próprio razão, preservando o modelo de extrato bancário: entrada soma, saída reduz.
- Os links das colunas de saída passam a conferir diretamente com os totais exibidos na Movimentação completa.
