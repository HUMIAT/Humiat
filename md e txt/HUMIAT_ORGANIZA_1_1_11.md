# HUMIAT Organiza 1.1.11

## Venda simplificada
- Nova venda solicita somente WhatsApp do cliente e equipamento.
- Se o WhatsApp ainda não existir, cria cadastro pendente com token público.
- Após salvar, exibe página com link público e botão para enviar o cadastro pelo WhatsApp.

## Cadastro público
- Cliente preenche CPF/CNPJ, e-mail e demais dados.
- CNPJ pode ser validado na própria página.
- Consulta de IE ficou mais tolerante a variações da resposta do provedor.
- Quando a situação fiscal/IE não puder ser confirmada, exibe: `SINTEGRA não disponível. Tente mais tarde.`
- CEP é o primeiro campo do endereço.
- Logradouro, bairro, município e UF vêm do CEP e ficam bloqueados.
- Cliente altera somente número e complemento.

## Endereço de entrega
- Opção `Endereço de entrega igual ao endereço do cliente` vem marcada por padrão.
- Ao desmarcar, abre um segundo endereço de entrega, também baseado no CEP.
- Banco recebe campos separados do endereço de entrega.

## NFA-e
- Se `Endereço de entrega igual ao endereço do cliente` estiver marcado, a nota usa o endereço do cliente.
- Se estiver desmarcado, a nota usa o endereço de entrega alternativo.
- Destino/CFOP também passam a considerar a UF do endereço efetivamente usado na nota.
- Payload atualizado para `ORGANIZA-NFAE-6`.
- Extensão Chrome incluída no pacote atualizada para 1.0.67.
