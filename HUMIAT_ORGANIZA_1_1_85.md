# Organiza 1.1.85

- Corrige falha de startup no PostgreSQL ao migrar `catalogo_impresso` de `Sim` para `Encadernado`.
- Em bases antigas, aumenta `equipamentos.catalogo_impresso` de `VARCHAR(10)` para `VARCHAR(20)` antes da atualização dos dados.
- Mantém o fluxo de vendas e pagamento da versão 1.1.84.
