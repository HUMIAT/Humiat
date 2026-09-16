# Alteração 1.1.11 — Cadastro público, endereço de entrega e NFA-e

Implementa o fluxo em que o atendente inicia a venda apenas com WhatsApp + equipamento e o cliente completa o cadastro pelo link público.

Regras principais:
1. Endereço do cliente é validado pelo CEP; logradouro/bairro/município/UF não são editáveis.
2. Número e complemento permanecem editáveis.
3. `Endereço de entrega igual ao endereço do cliente` fica marcado por padrão.
4. Ao desmarcar, o cliente informa um segundo CEP e os dados de entrega.
5. Na NFA-e, marcado usa o endereço do cliente; desmarcado usa o endereço de entrega.
6. Se a IE/situação ICMS não puder ser confirmada automaticamente, a tela informa `SINTEGRA não disponível. Tente mais tarde.`.
