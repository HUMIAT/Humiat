# HUMIAT / Organiza 1.2.18

## Duas rotinas de onboarding separadas

- **Usuários novos**: responsáveis por Empresa SolVoz. Conclui Humiat ID, Organiza/Tarefas rápidas, LokaFest/Usuário e SolVoz/Cliente Catálogo.
- **Pendências Humiat ID**: usuários já existentes no LokaFest que não possuem Empresa SolVoz. Conclui Humiat ID, Organiza/Tarefas rápidas e LokaFest/Usuário, sem conceder Cliente Catálogo.
- A fila de pendências deixa de confiar apenas no histórico da migração e passa a conferir o `humiat_user_id` real informado pelo LokaFest.
- Um cadastro que apareça como `Humiat ID: Não` no LokaFest volta a aparecer na fila até o vínculo local estar realmente gravado.
