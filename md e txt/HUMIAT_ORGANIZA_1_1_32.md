# HUMIAT / Organiza 1.1.32

- Corrige migração de senha para usuários Humiat ID que já existiam antes da ponte central.
- Se a senha do Humiat ID não validar, o login confere a senha atual do usuário central do Organiza pelo mesmo e-mail.
- Quando a senha do Organiza estiver correta, a identidade é migrada automaticamente para o hash atual do Humiat ID sem exigir redefinição manual.
- Mantém as permissões e vínculos já existentes.
