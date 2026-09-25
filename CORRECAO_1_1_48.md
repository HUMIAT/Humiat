# Organiza 1.1.48 — conferência e reenvio de campanha em Vendas

- A tela **Vendas** passa a exibir as campanhas de Atualização e permite escolher qual campanha deseja conferir.
- Novo filtro de envio: **Todos**, **Não enviado pelo Organiza**, **Enviado / processado** e **Fora da campanha**.
- Cada venda mostra o status do cliente na campanha selecionada.
- `PENDENTE`, `EM_ENVIO` e `IGNORADO` são tratados como não enviados pelo fluxo; `PROCESSADO` e `ENVIADO` aparecem como enviados/processados.
- O sistema deixa explícito que o Organiza registra a ação do fluxo, mas não recebe confirmação de entrega do WhatsApp.
- Clientes que participaram da campanha podem ser enviados ou reenviados manualmente a partir de Vendas.
- Antes de abrir o WhatsApp, o usuário vê a ficha do cliente em **modo somente consulta**, sem ações de edição.
- O WhatsApp não altera o status automaticamente no envio manual: depois de realmente enviar, o atendente confirma em **Confirmar que enviei** / **Confirmar reenvio manual**.
- O botão **Abrir** da tela de Vendas foi substituído por **Consultar**, levando primeiro à ficha somente leitura.
- A venda selecionada é destacada na ficha e todos os equipamentos ficam visíveis nessa consulta, independentemente do status da venda.
