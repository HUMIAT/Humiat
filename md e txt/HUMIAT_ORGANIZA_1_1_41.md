# HUMIAT / Organiza 1.1.41

- Campanhas calculam e salvam todas as mensagens antes de iniciar a fila.
- Botão único “WhatsApp e Próximo”: grava PROCESSADO antes de abrir o WhatsApp e avança automaticamente.
- Dois atendentes podem trabalhar simultaneamente; cada cliente é reservado atomicamente para apenas um usuário.
- Reservas abandonadas há mais de 30 minutos voltam à fila.
- Botão “Pular” não desativa o cliente de campanhas futuras.
- Pacotes pendentes passam a usar a lista real de versões do SolVoz, inclusive versões .3, eliminando divergência entre WhatsApp e popup.
- Contexto do cliente expõe ao SolVoz o snapshot de pacotes preparado para manter valores e quantidade idênticos.
