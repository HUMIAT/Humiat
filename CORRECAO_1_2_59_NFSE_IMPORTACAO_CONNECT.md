# Organiza 1.2.59 — correção da importação de NFS-e do Connect

## Falha
A rota `/organiza/nfse/importar-connect` persistia a URL integral da importação no campo `nfse_rascunhos.origem_url` (VARCHAR(500)). Com dados de evento longos, a inserção falhava com `psycopg.errors.StringDataRightTruncation` e HTTP 500.

## Correção
O campo guarda somente a URL da rota e os identificadores Connect de empresa/contrato. Os dados fiscais do cliente, evento e nota continuam sendo armazenados nos campos específicos. A referência externa `connect:empresa:contrato` permanece para impedir duplicatas ao reenviar.

## Teste de validação
1. No contrato 210, clicar em enviar NFS-e ao Organiza.
2. Verificar a abertura do rascunho no Organiza sem erro 500.
3. Repetir o envio e conferir que abre o mesmo rascunho, sem duplicar.
4. Conferir CNPJ, valores, descrição e endereço do evento antes da emissão.

Não há migração de banco necessária. Nenhuma nota foi emitida automaticamente.
