# HUMIAT Organiza 1.1.78

- Central Financeiro: manutenção só entra no saldo enviado ao Connect quando o orçamento estiver aprovado.
- A composição da manutenção continua exibindo também orçamentos ainda não aprovados para acompanhamento interno, identificados como “Aguardando aprovação”.
- InfinitePay integrada ao pagamento de Manutenção e Venda, preservando o pagamento manual.
- Cobranças InfinitePay são idempotentes: uma cobrança pendente é reutilizada em vez de criar duplicidade.
- Webhook/retorno InfinitePay registram automaticamente o pagamento na mesma tabela financeira já usada pelo Organiza.
- Data operacional dos novos pagamentos usa America/Sao_Paulo.
- Atualizações continuam usando a integração InfinitePay já existente via SolVoz.
