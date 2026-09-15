# HUMIAT Organiza 1.1.4 — Preenchimento automático NFA-e

- Adiciona integração local com a extensão Chrome "Organiza NFA-e".
- O botão "Preencher NFA-e automaticamente" envia o payload já conferido no Organiza para o Chrome.
- Se existir uma aba da SEFAZ aberta, ela é ativada; caso contrário, o portal da SEFAZ é aberto.
- A extensão preenche cabeçalho, destinatário, produto, tributos e pagamento conforme as telas/abas forem abertas.
- Mantém confirmação final (Validar/Salvar/Emitir) sob controle do usuário.
- Dados ficam em chrome.storage.local e expiram após 4 horas; há botão "Limpar dados".
- Corrige o antigo acesso direto à página interna da NFA-e que podia cair em erro de sessão.
- Versão visual: 1.1.4. Versão interna/API: 8.10.
