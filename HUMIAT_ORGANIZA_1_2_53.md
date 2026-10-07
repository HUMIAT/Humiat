# HUMIAT / Organiza 1.2.53

## Vendas — filtro por período

- Adicionados os filtros **Data inicial** e **Data final** na tela de Vendas.
- O período usa a **data da compra/venda (`data_compra`)**, a mesma referência do relatório Evolução de Vendas.
- Permite validar mês, trimestre ou intervalo livre, por exemplo **01/01/2025 a 31/03/2025**.
- Vendas sem data de compra não entram quando um período é informado.
- Se a data inicial for posterior à final, o sistema normaliza o intervalo automaticamente.
- O filtro é preservado ao abrir o **Relatório** e aparece no resumo dos filtros do relatório.
- Paginação e retorno para a tela de Vendas preservam o período selecionado.

Essa alteração não modifica vendas, pagamentos ou campanhas; apenas filtra a consulta.
