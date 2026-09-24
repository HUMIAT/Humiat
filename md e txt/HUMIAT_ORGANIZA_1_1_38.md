# HUMIAT / Organiza 1.1.38

- Lista de Clientes de Aluguel exclusiva da Karaokê RJ.
- Novo botão **Atualizar pelo Connect** para sincronização em lote dos contratos válidos da Karaokê RJ.
- Connect continua sendo a fonte prioritária quando um cliente já foi sincronizado.
- Campanhas de atualização agora geram o link SolVoz individualmente por cliente.
- O link começa na primeira versão que o equipamento ainda não possui e termina no pacote alvo da campanha.
- Exemplo: equipamento em 2025.2 e campanha 2026.1 -> `https://www.solvoz.com.br/atualizacoes/karaokerj/2026-1/2026-1`.
- Quando o cliente possui mais de um equipamento, usa-se o equipamento mais desatualizado para não omitir nenhuma versão.
