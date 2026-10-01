# Humiat / Organiza 1.2.14

- Corrige o fluxo de **Pendências Humiat ID** após aprovar um usuário.
- A aprovação mantém o modal aberto e recarrega a fila a partir do snapshot local já consultado.
- Somente o item aprovado sai da lista; as demais pendências continuam visíveis.
- A aprovação não invalida o snapshot da fila e não força nova consulta ao LokaFest.
- Erros e reenvio de e-mail também retornam para a mesma tela de pendências.
