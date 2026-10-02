# HUMIAT / Organiza 1.2.27

- Empresas SolVoz passam a armazenar uma miniatura local da logo oficial do SolVoz.
- A sincronização manual de Empresas SolVoz atualiza também as logos, sem consulta externa durante a navegação normal.
- O push de criação/atualização de empresa vindo do SolVoz tenta trazer a logo imediatamente.
- Nova API pública e leve para a LokaFest listar empresas ativas e consumir suas miniaturas locais.
- Tela Empresas SolVoz mostra a miniatura usada pela LokaFest e o estado da sincronização da logo.
- As miniaturas são normalizadas em WebP, até 260x120 px, para reduzir tráfego e custo de renderização.
