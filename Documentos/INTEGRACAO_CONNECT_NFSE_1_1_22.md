# Organiza 1.1.22 — importação NFS-e do Connect

- Nova rota `/organiza/nfse/importar-connect`.
- Recebe somente dados operacionais do contrato: cliente, datas, endereço do evento, equipamentos e valor.
- Cria/atualiza o cliente por CPF/CNPJ.
- Cria rascunho de NFS-e do tipo **Aluguel** com regras fiscais mantidas exclusivamente no Organiza.
- Usa `referencia_externa=connect:<empresa>:<contrato>` para impedir duplicidade.
- Se já existir rascunho para o contrato, abre o existente em vez de criar outro.
