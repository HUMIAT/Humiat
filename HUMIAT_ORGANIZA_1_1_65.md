# HUMIAT Organiza 1.1.65

## Estoque
- Novo menu **Estoque**.
- Entrada manual de itens com quantidade, custo unitário opcional e observação.
- Saldo separado em **Físico**, **Reservado** e **Disponível**.
- Vendas abertas passam a gerar saída automática da composição do equipamento e dos Opcionais.
- Manutenções ainda não aprovadas reservam os itens do orçamento sem baixar o físico.
- Ao aprovar a manutenção, a reserva é convertida em saída de estoque.
- Cancelamentos antes da aprovação liberam as reservas.
- Falta de saldo nunca bloqueia venda/manutenção; o saldo pode ficar negativo e é destacado.

## Controle por cor
Somente estes itens usam cor no estoque:
- BOTOES
- COOLER 12 MM
- FITA LED / LED

A quantidade continua definida pela composição/orçamento. A venda ou manutenção define as cores efetivamente usadas. Exemplo: BOTOES x3 pode consumir 1 AZUL, 1 VERMELHO e 1 ROSA. Mesmo sem ROSA em estoque, a operação salva e o saldo ROSA fica negativo.

## Vendas
- O botão **Editar equipamento** no menu Vendas abre exatamente o mesmo cadastro completo usado dentro do Cliente.
- Ao salvar a mesma ficha, composição, Opcionais e distribuição das cores sincronizam o estoque sem duplicar saída.

## Migração
- Não baixa retroativamente vendas antigas já entregues antes da implantação do estoque.
- Sincroniza apenas vendas ainda em produção e manutenções abertas na primeira inicialização da versão.
