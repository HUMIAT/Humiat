# Humiat / Organiza 1.2.10

- Corrige erro 500 no `/painel` administrativo após a revisão das pendências LokaFest.
- Corrige consulta de e-mail SolVoz incompatível com PostgreSQL (`COALESCE(timestamp, '')`).
- Isola consultas opcionais de conciliação em SAVEPOINTs para impedir que uma falha secundária deixe a sessão SQL em `InFailedSqlTransaction`.
- Mantém as regras de pendências, usuários novos e envio de primeiro acesso da 1.2.09.
