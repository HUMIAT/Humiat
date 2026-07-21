# Correção — contadores, comunicação e execução

## Problemas corrigidos

- A quantidade exibida no painel não correspondia à lista aberta.
- O filtro `status=execucao` não existia e, por isso, mostrava todas as manutenções.
- A tela de aguardando aprovação não exibia claramente o estado da comunicação.

## Regra aplicada

A listagem agora utiliza `etapa_manutencao()` — a mesma fonte de verdade do painel.

- `aceite` e `aprovacao`: somente etapa 3.
- `execucao` e `producao`: somente etapa 5.
- Demais filtros também foram alinhados às etapas operacionais.
- Na aprovação, cada manutenção mostra `Comunicado` ou `Não comunicado`.
- Orçamentos comunicados oferecem a ação `Reenviar`.
- Orçamentos ainda não comunicados oferecem `Enviar WhatsApp`.

Assim, o contador do painel e o conteúdo da tela permanecem sincronizados.
