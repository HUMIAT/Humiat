# Organiza → Connect

## Fluxo operacional

1. Em **Vendas**, abra **Registrar pagamento** e cadastre cada recebimento separadamente.
   - Cada pagamento possui valor, data e banco próprios.
   - Uma venda pode ter vários pagamentos.
2. Em **Manutenção**, o registro de pagamento existente agora também pede o banco.
3. Abra **Central financeiro**.
4. Clique em **Enviar tudo**.
   - Registros novos são enviados.
   - Registros alterados desde o último envio são reenviados.
   - Registros sem alteração ficam como `Enviado` e não são duplicados.

## Configuração no Render do Organiza

Configure:

- `CONNECT_API_URL` = URL pública do Connect, por exemplo `https://seu-connect.onrender.com`
- `CONNECT_API_KEY` = mesma chave configurada no Connect como `ORGANIZA_API_KEY`

O Organiza envia para:

`POST /api/integracoes/organiza/lancamentos`

## Identificadores enviados

Venda:
`ORGANIZA-VENDA-PAG-{id_do_pagamento}`

Manutenção:
`ORGANIZA-MANUTENCAO-PAG-{id_do_pagamento}`

Esses identificadores são estáveis. O Connect atualiza o registro existente ao receber novamente o mesmo `id_externo`.


## Agrupamento manual

Na Central Financeiro, selecione os lançamentos e use **Agrupar e enviar**.

O Organiza agrupa automaticamente somente registros compatíveis:
- mesmo cliente;
- mesmo tipo (`venda` ou `manutencao`);
- mesma data de pagamento;
- mesmo banco.

Exemplo: três manutenções do mesmo cliente de R$ 30,00 + R$ 100,00 + R$ 50,00,
com a mesma data e banco, são enviadas ao Connect como um único lançamento de R$ 180,00.

Os registros originais continuam separados no Organiza e ficam marcados individualmente como enviados.
