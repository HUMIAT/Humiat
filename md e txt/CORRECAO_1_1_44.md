# Organiza 1.1.44 — Campanha preparada antes do envio

- A campanha inteira é calculada antes do primeiro contato: telefone, mensagem, link, pacotes, valor normal e valor promocional.
- Para campanhas de atualização, o SolVoz é consultado somente na preparação inicial; o snapshot retornado é reutilizado para todos os clientes.
- Durante o envio não há consulta ao SolVoz nem montagem de mensagem por contato.
- A reserva do contato continua sendo gravada antes da tela de envio aparecer, impedindo que dois atendentes diferentes recebam o mesmo contato.
- O botão "WhatsApp e Próximo" abre o WhatsApp diretamente, sem página intermediária "Abrindo WhatsApp" e sem estado "Salvando...".
- O status PROCESSADO é enviado em segundo plano; a própria navegação para o próximo contato também confirma localmente o contato anterior, tornando a operação idempotente.
- A tela do próximo contato trabalha apenas com snapshots já persistidos no banco local do Organiza.
- Campanhas antigas sem snapshot são preparadas por inteiro uma única vez antes de exibir o primeiro contato.
