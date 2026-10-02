# HUMIAT / Organiza 1.2.17

- Onboarding de responsável de Empresa SolVoz passa a ser completo em uma única ação.
- Novo responsável recebe Humiat ID ativo, Organiza/Tarefas rápidas, LokaFest/Usuário e SolVoz/Cliente Catálogo.
- O Humiat cria ou atualiza o cadastro do LokaFest, aprova o usuário e grava o `humiat_user_id` local.
- A busca de Usuários novos também mostra cadastros antigos parcialmente configurados para permitir reparo.
- Migração antiga do LokaFest reaplica a rotina completa antes de considerar o usuário regular.
- E-mails enviados pelo Humiat passam a enviar cópia oculta para `HUMIAT_EMAIL_COPIA`; se ausente, usa `HUMIAT_ADMIN_EMAIL`.
- O botão manual Salvar acessos continua disponível para ajustes, mas não é mais necessário no onboarding padrão de Empresa SolVoz.
