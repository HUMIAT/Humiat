# HUMIAT / ORGANIZA 1.1.33

## Humiat ID centralizado no cadastro do Cliente

O cadastro de Cliente do Organiza passa a ser a fonte operacional dos acessos do usuário.

### Cadastro do cliente
- Reaproveita nome, empresa, e-mail, telefone e demais dados já existentes.
- Cada Cliente pode apontar para um único `humiat_usuario_id`.
- Ao salvar os acessos, o Organiza reutiliza o Humiat ID existente pelo vínculo/e-mail ou cria a identidade central se ainda não existir.
- Não cria uma senha separada por sistema.

### Permissões no próprio Cliente
No detalhe do Cliente existe o card **Humiat ID**, onde o administrador escolhe o que aquela pessoa pode acessar.

- SolVoz: Cliente Site, Cliente Catálogo e, para equipe interna, ADM SolVoz.
- Organiza / Connect / LokaFest e futuros produtos: Usuário e ADM conforme os produtos cadastrados no Humiat ID.
- Desativar o Humiat ID bloqueia o login sem apagar o cadastro nem as permissões.

### Reenviar link de acesso
O botão **Reenviar link de acesso** cria um novo link Humiat ID de uso único, invalida links anteriores não usados e envia por e-mail.
O cliente refaz uma única senha e continua com todos os sistemas já liberados no cadastro.

### Piloto da equipe interna
Junior, Débora e Luiz continuam usando a identidade administrativa já existente, mas essa mesma identidade pode ficar vinculada a uma ficha de Cliente para validar o fluxo real. Não é criada uma segunda identidade.

### SolVoz
- Cliente Site é a área do comprador/site.
- Cliente Catálogo é a área de assinatura/renovação do SolVoz.
- O contexto de empresa do Cliente Catálogo segue a empresa SolVoz vinculada aos equipamentos.
- A Karaokê RJ é adicionada como contexto quando Cliente Site está liberado, evitando abrir renovação na empresa errada.

### Hub Humiat
O cliente vê somente os produtos e perfis liberados no cadastro do Cliente no Organiza.
