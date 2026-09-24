# Organiza 1.1.36 — Lista de Clientes de Aluguel

- Adiciona a segunda lista do módulo de Campanhas: **Clientes de Aluguel**.
- A lista de aluguel é separada do cadastro operacional de Clientes do Organiza.
- Importação por CSV usando `NOME` e `WHATTSAPP`/`WHATSAPP`; a coluna `TIPO` pode existir e é ignorada.
- Duplicidade controlada pelo telefone internacional normalizado.
- Reimportar o mesmo telefone não cria duplicidade e não reativa quem foi inativado para campanhas.
- Ativar/inativar afeta somente campanhas.
- Nova campanha pode escolher **Clientes de Atualização** ou **Clientes de Aluguel**.
- Mantido o envio manual pelo WhatsApp com reserva para uso simultâneo por mais de um usuário.
- A planilha fornecida possui 512 linhas; 511 telefones são reconhecidos automaticamente pelas regras atuais. Um contato sem DDD precisa ser corrigido na planilha antes da importação.
