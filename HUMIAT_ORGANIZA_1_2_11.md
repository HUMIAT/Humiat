# Humiat / Organiza 1.2.11

- Usa o WhatsApp como chave principal de conciliação LokaFest -> Organiza, com CPF/CNPJ apenas como fallback.
- Aplica a mesma prioridade por telefone na integração que fornece equipamentos do Organiza ao LokaFest.
- O primeiro acesso usa exclusivamente o e-mail cadastrado no Organiza.
- Quando o Organiza não possui e-mail válido, a pendência exibe **Enviar cadastro** e abre o WhatsApp com a ficha pública do cliente para atualização.
- O vínculo Humiat não é criado antes de existir e-mail no Organiza, preservando o acesso atual do usuário.
- Ao aprovar uma pendência existente do LokaFest, apenas vincula o Humiat ao perfil já existente; não tenta recriar o usuário LokaFest.
