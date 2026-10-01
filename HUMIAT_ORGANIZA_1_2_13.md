# Humiat / Organiza 1.2.13

- Humiat ID abre em modo leve: a abertura normal consulta apenas produtos/permissões do usuário atual; usuários, novos responsáveis e pendências LokaFest ficam sob demanda.
- Painel Humiat exibe imediatamente “Aguarde, carregando dados...” e mostra carregamento também ao abrir rotinas manuais.
- “Usuários novos” passa a consultar somente responsáveis definidos em **Empresas SolVoz**; clientes comuns da Karaokê RJ não viram Humiat ID.
- Empresas SolVoz deixam de fazer N+1 na listagem: clientes, responsáveis e divergências de equipamentos são carregados em lote.
- Adiciona `solvoz_id` estável no Organiza, além de responsável por cliente/Humiat ID.
- SolVoz passa a ser fonte mestre de nome, slug e status; Organiza mantém somente o alias legado do Connect e os vínculos operacionais.
- Adiciona botão **Atualizar empresas do SolVoz** para reconciliação manual do cadastro existente.
- Toda empresa pode ter um responsável explícito. Para empresa de cliente, se o responsável ainda não tiver Humiat ID, ele é criado somente nesse caso e recebe acesso SolVoz Catálogo.
- Karaokê RJ aceita como responsável somente a equipe interna Humiat (Junior, Débora ou Luiz); clientes comuns não recebem usuário por esse vínculo.
- Se o responsável possuir equipamento em outra Empresa SolVoz, a tela sinaliza a divergência e oferece **Corrigir vínculo**.
- Novos salvamentos de equipamento respeitam automaticamente a Empresa SolVoz do responsável, impedindo nova divergência.
- Nome/slug/status recebidos do SolVoz atualizam a mesma empresa no Organiza pelo ID estável, preservando equipamentos e histórico.
