# Organiza 1.1.51 — consistência de pacotes e lote de não enviados

- Nenhum equipamento pode permanecer sem pacote.
- No deploy, uma rotina idempotente revisa os cadastros existentes: se a máquina já possui pacote, ele é preservado; se estiver vazio, recebe o primeiro pacote disponível.
- O pacote do cliente é sincronizado pela máquina ativa mais antiga. Se não houver máquina ativa, usa a máquina mais antiga do cadastro.
- `falta_pacote` do equipamento e do cliente é recalculado automaticamente.
- O formulário de equipamento não oferece mais a opção `Sem pacote` e passa a aceitar todas as versões válidas existentes, inclusive versões como `2022.3`, `2023.3` e `2023.4`.
- A importação de implantação também executa a mesma rotina de consistência ao final.
- Campanhas de Atualização ganharam o botão `Criar lote de não enviados`.
- O lote complementar reutiliza a rotina de lotes já existente e adiciona clientes atualmente elegíveis que ficaram fora da campanha ou ainda estão pendentes/pulados, sem duplicar quem já está como `PROCESSADO` ou `ENVIADO`.
- Lotes complementares recebem novos números e não renumeram os lotes originais.
