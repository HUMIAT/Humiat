# HUMIAT Organiza 1.1.3 — Padrão fiscal NFA-e

- Padrão fiscal fixo para vendas: NCM 95045000, SEM GTIN, UN, origem 0, CSOSN 400, PIS 07 e COFINS 06.
- CFOP automático: 5102 para destinatário RJ e 6102 para outra UF.
- Código e descrição fiscal são os únicos dados do produto editáveis por equipamento.
- Padrões iniciais: 00001 Jukebox, 00002 Maletaokê, 00003 Karaokê iPhone, 00004 Fliperama.
- Tela “Preparar NFA-e” com validação dos dados do cliente, produto, valor e pagamentos.
- JSON estável para futura automação/extensão do navegador, além de XML e CSV de apoio.
- ViaCEP passa a guardar também o código IBGE do município nas próximas consultas.
- Versão visual: 1.1.3. Versão interna/API: 8.9.
