# HUMIAT Organiza 1.2.43

## Agenda — leitura direta do Google Agenda

- A visão mensal do Organiza passa a consultar diretamente o calendário Google conectado da empresa no mês exibido.
- A consulta usa somente os dados que realmente existem no Google Agenda: título, data/hora, descrição, local e link do evento.
- Eventos com o padrão atual do Connect são identificados pela própria informação retornada pelo Google (`conect.humiat.com.br` / `CONTRATO #`) e aparecem na categoria **Connect**.
- Outros compromissos existentes no calendário aparecem na categoria **Google Agenda**.
- Nenhuma informação de contrato, cliente, pagamento ou equipamento é consultada no Connect.
- Eventos que o próprio Organiza já conhece pelo `google_event_id` não são repetidos na categoria Google, evitando duplicidade visual.
- Eventos recorrentes são expandidos pelo Google para as datas do mês.
- Eventos de dia inteiro são suportados.
- O detalhe do dia mostra exatamente título, endereço/local e descrição devolvidos pelo Google e oferece **Abrir no Google**.
- Eventos lidos do Google são somente visualizados no Organiza; não é criado registro local novo.
- Se a consulta ao Google falhar, a agenda local continua funcionando e exibe o erro sem derrubar a página.
