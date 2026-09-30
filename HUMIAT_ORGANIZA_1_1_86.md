# Organiza 1.1.86

Ajuste do tomador recebido do Connect.

- O Organiza passa a receber `cliente_nome` e `cliente_telefone` junto com o CNPJ da NFS-e.
- Cliente novo é criado com nome e WhatsApp do contrato, evitando `CNPJ ...` e telefone `00000000000`.
- Cadastros provisórios já criados anteriormente são corrigidos automaticamente quando o mesmo contrato/CNPJ for enviado novamente.
- Razão social, nome fantasia, inscrição estadual, situação cadastral, ICMS e endereço fiscal continuam sendo mantidos pelo Organiza por CNPJ.
- Se o Organiza já tiver nome/telefone reais no cadastro, esses dados são preservados e não são sobrescritos pelo Connect.
