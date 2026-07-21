# Fluxo guiado de manutenção

## Alterações

- Etapa 4 exige pagamento, prazo e confirmação por WhatsApp.
- O envio da confirmação registra a comunicação e libera a Etapa 5.
- Etapa 5 passou a representar a execução do serviço.
- Serviço pode ser pausado para compra de peça, com motivo, previsão e comunicação ao cliente.
- Serviço pausado não pode ser concluído.
- Para concluir, é obrigatório preencher o diagnóstico final.
- A comunicação de serviço concluído libera a Etapa 6.
- Etapas seguintes permanecem bloqueadas até a conclusão dos requisitos.
- Cada etapa mostra claramente o próximo passo e o que falta concluir.
- Migração automática adiciona os novos campos ao SQLite e PostgreSQL.
