# HUMIAT / Organiza 1.2.36

## Estoque mínimo e relatório de compras

- Corrige a migração do **estoque mínimo** que foi separado da contagem física na 1.2.26.
- Valores de mínimo já existentes na antiga contagem são copiados uma única vez para `estoque_minimos`.
- Mínimos que já tenham sido configurados na tela **Itens** não são sobrescritos.
- Salvar uma contagem física não apaga mais o campo legado antes da migração.
- **Posição do estoque** e **Relatório de compras** continuam usando a mesma função `estoque_saldos`, portanto fecham na mesma base: `Disponível = Físico - Vendas reservadas`.
- Manutenção aprovada permanece como saída física; manutenção não aprovada não reserva nem movimenta.
- **Comprar = máximo(Estoque mínimo - Disponível, 0)**. Com mínimo 3 e disponível 0, o relatório passa a sugerir compra de 3 unidades.
