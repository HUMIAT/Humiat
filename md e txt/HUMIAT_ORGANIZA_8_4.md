# HUMIAT Organiza 8.4

## Objetivo
Finalizar o ADM unificado com a área de máquinas mais simples e recuperar o acesso ao diagnóstico técnico do SolVoz/Render.

## Alterações
- Removido o indicador `online agora` da área **Máquinas do Organiza**.
- A área agora mostra somente máquinas vinculadas, registros no SolVoz e última sincronização.
- Data de sincronização exibida no formato brasileiro e convertida para horário de Brasília.
- Adicionado botão **Diagnósticos** no cabeçalho da empresa do ADM Humiat.
- O botão é visível apenas para Administrador Humiat e abre o painel técnico do SolVoz/Render.

## Diagnóstico
O atalho `/admin-humiat/diagnosticos-solvoz` redireciona para o painel técnico configurado por `HUMIAT_SOLVOZ_DIAGNOSTICS_PATH`.
Padrão: `/_sv/uso/7f29c4b8`.
