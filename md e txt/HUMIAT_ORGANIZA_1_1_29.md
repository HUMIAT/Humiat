# HUMIAT / ORGANIZA 1.1.29

- Humiat externo passa a funcionar como hub: Usuários + ADM SolVoz.
- Rotinas globais do SolVoz ficam dentro do próprio ADM SolVoz.
- Piloto Humiat ID para administradores:
  - importa/vincula ADMs já existentes no Organiza pelo e-mail;
  - se o usuário central não existir no Organiza, cria o cadastro mínimo;
  - mantém uma única identidade e senha;
  - permissões SolVoz separadas em `Sistema` e `ADM`;
  - acesso ao ADM SolVoz é validado antes da emissão do ticket SSO;
  - retirar ADM não remove o usuário nem o acesso ao sistema.
- Nova tabela `humiat_usuario_produtos` para permissões por usuário/produto.
- Usuários novos sem permissão marcada permanecem sem acesso após reinício.
- Nesta etapa, clientes finais ainda não foram migrados para o login único.
