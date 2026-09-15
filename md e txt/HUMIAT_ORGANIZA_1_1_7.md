# HUMIAT Organiza 1.1.7

- Consulta automática de CNPJ no cadastro usando CNPJ.ws em consulta pontual.
- Cadastro público: cliente informa seus dados de contato e CPF/CNPJ; para CNPJ, Razão Social, nome fantasia, endereço, IE e situação cadastral são consultados automaticamente.
- Cadastro público não recebe nem exibe NCM, CFOP ou qualquer informação fiscal de produto.
- Cadastro interno: botão "Atualizar cadastro pelo CNPJ" na ficha do cliente.
- Novos campos: situação ICMS, situação cadastral do CNPJ, fonte e data da última consulta.
- IE ativa retornada pelo cadastro estadual é tratada como contribuinte; ausência de IE fica como "Não confirmado", sem adivinhação.
- NFA-e passa a bloquear CNPJ com situação ICMS não confirmada antes do preenchimento fiscal.
- Padrão tributário de venda MEI ajustado para CSOSN 102, PIS 07 e COFINS 07.
- Estrutura Organiza 8.13.
