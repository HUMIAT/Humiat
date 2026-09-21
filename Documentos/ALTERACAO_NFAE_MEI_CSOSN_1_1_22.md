# Organiza 1.1.22 — NFA-e MEI

- Mantido o padrão fiscal automático de venda MEI: Origem 0, CSOSN 102, PIS 07 e COFINS 07.
- Corrigido o texto exibido no cadastro do equipamento, que ainda mostrava o padrão legado CSOSN 400 / COFINS 06.
- O cliente sem IE continua sendo enviado conforme sua situação ICMS cadastrada; não é criada inscrição estadual fictícia.
- Venda RJ usa CFOP 5102 e venda para outra UF usa CFOP 6102 conforme o fluxo atual da NFA-e.
- A tributação permanece interna/automática; o usuário não precisa cadastrar alíquota ou base de ICMS no equipamento.
