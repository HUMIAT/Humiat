# Correção do erro ao atualizar itens aprovados

## Causa
O campo `assistencia_orcamentos.status` possui limite de 40 caracteres.
A correção administrativa gravava um texto maior que esse limite, provocando
erro 500 no PostgreSQL do Render.

## Correção
- status reduzido para `Aprovado: todos` ou `Aprovado: obrigatórios`;
- origem e data da alteração preservadas em `observacao`;
- atualização continua substituindo os itens aprovados sem duplicar registros;
- etapa atual da manutenção é mantida.
