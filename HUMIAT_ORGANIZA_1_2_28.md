# HUMIAT / Organiza 1.2.28

## Unidade por Item

- Adiciona `unidade` ao cadastro de Itens.
- Unidades disponíveis inicialmente: `UN` e `M`.
- Migração inicial preenche todos os itens existentes com `UN`.
- `CABO BIPOLAR` e `FITA LED` entram inicialmente como `M`.
- Depois da primeira migração, a unidade pertence ao Item e pode ser alterada manualmente; categoria ou nome não voltam a sobrescrevê-la.
- Novos itens usam `UN` por padrão.

## Telas e relatórios

A unidade passa a aparecer em:

- Cadastro de Itens;
- Estoque;
- Relatório de reposição / Compras;
- Compras aguardando chegada;
- Contagem física;
- Movimentações por item;
- CSV de movimentações;
- CSV de contagem.

A quantidade continua numérica/decimal, permitindo registrar metros quando a unidade for `M`.
