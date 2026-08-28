# HUMIAT / Organiza 8.7 — acesso por vínculo de empresa

## Regra final

- Usuário **sem empresa vinculada** no Humiat ID = **equipe interna / perfil completo**.
- Usuário **com empresa vinculada** = **Área da Empresa**, restrita ao SolVoz daquela empresa.
- O campo legado `tipo` permanece no banco apenas por compatibilidade e não é mais o critério principal de autorização.

## Equipe interna existente

No startup da 8.7, vínculos indevidos dos usuários legados do Organiza `Junior`, `Debora` e `Luiz` são removidos automaticamente. Também são reconhecidos por padrão os e-mails `jr.delphi@gmail.com` e `deborapavonerabello@gmail.com`.

Para acrescentar outros usuários internos sem alterar o código, podem ser usadas as variáveis:

- `HUMIAT_EQUIPE_INTERNA_USUARIOS=Junior,Debora,Luiz`
- `HUMIAT_EQUIPE_INTERNA_EMAILS=email1@dominio.com,email2@dominio.com`

## Proteções

- A automação Organiza -> SolVoz não vincula uma conta existente que já esteja sem empresa; ela é preservada como equipe interna.
- Um usuário interno não consegue vincular o próprio usuário a uma empresa pelo painel, evitando perda acidental do perfil completo.
- Tickets SSO da equipe interna são emitidos sem `empresa_id`; tickets de clientes levam a empresa vinculada.
