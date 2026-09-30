# HUMIAT Organiza 1.1.82

## NFS-e / Connect
- Importação do Connect passa a exigir CNPJ do tomador e não altera o cadastro fiscal do cliente.
- Se o CNPJ não existir, o Organiza cria cadastro mínimo e sinaliza que os dados empresariais precisam ser atualizados.
- O Connect é a fonte de verdade para todos os dados do evento: datas, descrição e endereço.
- Rascunho mantém `origem=connect` e referência `connect:empresa:contrato` para evitar duplicidade.
- Correção da identificação visual da origem Connect na lista.

## Limpeza de rascunhos
- Rascunhos locais e rascunhos preparados no portal podem ser excluídos do Organiza.
- NFS-e com status `EMITIDA` não pode ser excluída.
- Incluído botão para limpar em lote somente registros não emitidos.
- A exclusão local não tenta apagar rascunhos no Portal Nacional.
