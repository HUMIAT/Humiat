# Organiza 8.6 — Automação SolVoz

- Empresas clonadas no SolVoz passam a ser criadas automaticamente em `solvoz_empresas` no Organiza via integração servidor-servidor.
- A tela Empresas SolVoz deixa de exigir cadastro manual e passa a mostrar os clientes encontrados pelos equipamentos vinculados.
- Novo botão **Criar / reenviar acesso** usa nome, CPF/CNPJ, e-mail e telefone já cadastrados no cliente do Organiza.
- O usuário criado é `CLIENTE_EMPRESA` no Humiat ID e recebe somente o produto SolVoz; não recebe acesso ao Organiza.
- O primeiro acesso é enviado por e-mail com link de uso único para o cliente definir a própria senha.
- O perfil legado `ADMIN_EMPRESA` é migrado para `CLIENTE_EMPRESA` sem perder vínculos.
- Administradores internos Humiat continuam com acesso completo.
