# Organiza 1.1.47 — seleção explícita de lote

- Qualquer campanha cria os destinatários, mensagens e lotes de até 100 no momento em que é salva.
- Campanhas de Atualização consultam o SolVoz somente durante essa preparação; o envio dos lotes usa apenas o snapshot local.
- Antes de iniciar ou continuar uma campanha, o atendente precisa escolher explicitamente um lote.
- O lote escolhido é reservado atomicamente para o atendente; outro usuário não consegue abrir o mesmo lote.
- Cada atendente trabalha em apenas um lote por vez dentro da mesma campanha.
- Lotes ocupados mostram o nome do atendente; lotes concluídos ficam indisponíveis.
- Ao concluir um lote, a tela volta para a campanha para escolher o próximo, sem seleção automática.
- Mantida a fila instantânea: os contatos do lote já chegam prontos ao navegador e WhatsApp/Pular avançam localmente sem recarregar contato por contato.
- Edição de campanha ainda em rascunho recria os lotes e snapshots para manter mensagem, público e links coerentes.
