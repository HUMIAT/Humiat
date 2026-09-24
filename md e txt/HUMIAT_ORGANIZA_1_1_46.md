# HUMIAT / Organiza 1.1.46

- Regra padrão para qualquer campanha: quebra automática em lotes de no máximo 100 contatos.
- Cada atendente reserva um lote inteiro; dois usuários não trabalham no mesmo lote.
- Toda a campanha continua sendo preparada antes do primeiro envio, sem consultar sistemas externos durante a fila.
- Os até 100 contatos do lote são carregados de uma vez na página.
- WhatsApp e Próximo muda imediatamente o card para o próximo contato e abre o WhatsApp da mensagem anterior.
- Pular também avança instantaneamente, sem recarregar a página.
- Gravações de PROCESSADO/IGNORADO acontecem em segundo plano no Organiza.
- Só há nova carga de página ao terminar um lote de 100 e abrir o próximo lote.
- Mantido psycopg 3 para compatibilidade com Render/PostgreSQL.
