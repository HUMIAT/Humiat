# CORREÇÃO — ETAPAS, CORES E AGENDA

## Fonte única de verdade
A etapa atual agora é calculada por `etapa_manutencao()` e reutilizada no painel, agenda e detalhe da manutenção.

## Cores
- Azul: ação da equipe.
- Laranja: ação do cliente.
- Verde: vendas.
- Cinza: encerrado.

## Agenda
- Filtros por etapa.
- Cards agrupados por etapa.
- Clique abre a manutenção diretamente na etapa correspondente.
- Cada card usa o mesmo nome e a mesma cor do painel e do detalhe.
- A data exibida é a data mais representativa da etapa atual.

## Painel
- Não depende mais apenas do texto salvo em `status`.
- Se a manutenção já avançou para execução, ela aparece na Etapa 5 mesmo que um status antigo tenha ficado gravado.
- A Etapa 1 é laranja porque aguarda o cliente trazer o equipamento.

## Tela da manutenção
- A barra de etapas usa as mesmas cores oficiais.
- Etapas concluídas não ficam mais verdes; preservam a cor de responsabilidade.
