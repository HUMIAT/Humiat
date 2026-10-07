# HUMIAT / Organiza 1.2.55

## Campanha pode ser corrigida depois de iniciada
- O botão de edição passa a aparecer também para campanhas ATIVAS e FINALIZADAS.
- Depois que a campanha começou, lista e mês continuam protegidos contra alteração.
- Nome, mensagem e link fixo podem ser corrigidos.
- Ao salvar uma campanha já iniciada, o Organiza recalcula somente os snapshots PENDENTE e EM_ENVIO.
- Mensagens PROCESSADAS, ENVIADAS ou IGNORADAS permanecem intactas no histórico.
- Lotes, reservas e status não são zerados.

## Promoção até 31/10/2026
- A data local da campanha de aniversário foi atualizada de 10/10/2026 para 31/10/2026.
- Referências antigas 30/09, 05/10 e 10/10/2026 dentro do texto são normalizadas para 31/10/2026 na geração das novas mensagens.
- Ao salvar a campanha ativa, os destinatários ainda pendentes recebem a mensagem recalculada com a data correta.

## Versão
- ORGANIZA_VERSAO: 1.2.55
