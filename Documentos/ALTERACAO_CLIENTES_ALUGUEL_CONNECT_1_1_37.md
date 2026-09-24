# Organiza 1.1.37 — Clientes de Aluguel + Connect

A planilha é a carga histórica. O Connect é a fonte de dados atual. Quando um cliente do Connect coincide pelo telefone ou pelo connect_cliente_id, o mesmo registro é atualizado e marcado como Integração = Connect. O Connect nunca é sobrescrito por nova importação de planilha.

Campos principais: nome sem acentuação, telefone normalizado, último mês/data de aluguel, integração, última sincronização e IDs de vínculo do Connect.

A campanha de aluguel pode usar todos os clientes ou somente o mês escolhido.
