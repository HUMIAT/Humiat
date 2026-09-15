# HUMIAT Organiza 1.1.5

## NFA-e — correção do preenchimento por aba

- Emitente protegido: a extensão não altera nenhum dado do emissor.
- Aba NF-e mapeada com os padrões da operação de venda.
- Destinatário separado automaticamente em CPF (11 dígitos) ou CNPJ (14 dígitos).
- Inscrição Estadual / Não Contribuinte tratada conforme o cadastro do cliente.
- UF é preenchida antes do Município para respeitar o carregamento da lista da SEFAZ.
- Produtos e Serviços e Pagamento tratados como janelas independentes abertas pelo botão Incluir.
- Tributos: origem 0, CSOSN 400, PIS 07 e COFINS 06; demais valores ficam para a SEFAZ.
- Total, Transporte, Referências e Cobrança não são alterados.
- Observação fiscal e informação adicional do produto são montadas pelo Organiza com equipamento + atualização cadastrada.
- A extensão não valida, salva ou emite a nota. A validação final permanece na SEFAZ.
- Extensão Chrome incluída no pacote: versão 1.0.2.
