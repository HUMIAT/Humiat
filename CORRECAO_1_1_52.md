# Organiza 1.1.52 — lote complementar padrão, filtros e desempenho

## Campanhas
- "Criar lote de não enviados" passa a ser regra geral para campanhas de Atualização e Aluguel.
- A opção aparece quando há elegíveis que ainda não possuem caminho de envio em um lote aberto.
- Não duplica PROCESSADO/ENVIADO, não recupera IGNORADO e não move EM_ENVIO.
- Um PENDENTE que já esteja em lote aberto não é transferido novamente para outro lote.
- Contatos novos/elegíveis que ficaram fora da campanha podem formar lote complementar de até 100.

## Desempenho
- A criação do lote não executa mais a correção completa de pacotes em toda a base.
- O backfill de consistência de pacotes foi otimizado para carregar clientes/equipamentos em lote, eliminando o N+1 de consultas.
- A preparação do lote usa apenas os destinatários necessários e uma única consulta de configuração do SolVoz para campanhas de atualização.

## Listas de clientes
- Clientes de Atualização: filtro por nome/telefone e Ativo/Inativo.
- Clientes de Aluguel: filtro por nome/telefone e Ativo/Inativo, mantendo mês e integração.
- Ao ativar/inativar um contato, os filtros atuais são preservados.
- Layout dos filtros ajustado para não sobrepor campos em desktop, tablet ou celular.
