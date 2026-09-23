# Alteração — Humiat ID central no Cliente — 1.1.33

Objetivo: transformar o cadastro de Cliente do Organiza no ponto único de atendimento para identidade e permissões.

Fluxo operacional:
1. Localizar o Cliente no Organiza.
2. Conferir/editar e-mail e telefone já existentes.
3. No card Humiat ID, marcar os sistemas/perfis permitidos e salvar.
4. Se a identidade ainda não existir, o Organiza cria/reutiliza o Humiat ID e envia o primeiro acesso.
5. Em caso de perda de acesso, usar Reenviar link de acesso. O cliente redefine a senha única e mantém as permissões.

A implementação preserva as rotinas antigas do SolVoz para compatibilidade, mas o fluxo visível do Cliente passa a usar Humiat ID.
