# HUMIAT / Organiza 1.2.09

## Pendências LokaFest revisadas

- Usuário que já possui Humiat ID com acesso de Usuário ou ADM no LokaFest não aparece mais como pendência.
- A conciliação continua usando CPF e WhatsApp normalizados para localizar o mesmo cliente no Organiza.
- O e-mail para primeiro acesso usa, nesta ordem: Organiza, LokaFest quando a API disponibilizar o campo, e o cache de acesso SolVoz do mesmo cliente.
- Se a tela estiver desatualizada e alguém já regular for aprovado, o sistema apenas retira a pendência; não recria acesso nem envia e-mail indevido.
- Cliente Catálogo passa a ser o perfil padrão do SolVoz nas rotinas automáticas de cliente Humiat; o vínculo com empresa continua sendo acrescentado quando identificado pelos equipamentos.
- Mantém o envio de primeiro acesso no fluxo de aprovação quando a migração é realmente necessária.
