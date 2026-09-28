# HUMIAT / Organiza 1.1.62

## Custo de produção e margem das vendas

- Cadastro mestre de **Equipamentos de venda**, alinhado aos nomes e slugs do SolVoz.
- SKU fixo disponível para o futuro vínculo Organiza → SolVoz.
- Composição de cada equipamento vinculada diretamente aos **Itens do Organiza** com quantidade editável.
- A planilha `chat(1).xlsx` foi usada somente para as **quantidades**. Nenhum preço/custo da planilha é importado.
- Regra aplicada em todas as composições importadas: **Amplificador Stereo → Amplificador Mono C/ Bluetooth**.
- Microfone e Catálogo Encadernado saíram da composição-base importada porque passam a entrar conforme os **Opcionais** selecionados.
- **Som** saiu dos Opcionais: Premium/JBL pertence ao próprio equipamento comercial.
- Cadastro de **Opcionais** liga cada escolha diretamente a um Item do Organiza e permite configurar quantidade.
- Catálogo da venda: **Básico** ou **Plus**, com Plus = **+ R$ 400,00**.
- Nova venda passa a selecionar o equipamento comercial e o catálogo.
- Vendas calculam automaticamente **custo-base + opcionais = custo final**, além de **lucro e margem**.
- Ao finalizar como **Entregue** ou **Vendido**, o Organiza grava snapshot de custo/preço/lucro/margem para preservar o histórico.
- A tela de Vendas passa a exibir preço da venda, custo final, lucro e margem.

## Composições iniciais

Foram carregadas as quantidades compatíveis da planilha para Karaokê Portátil, Bipartidos 17/19, Guitarrinhas 19 e iPhone 17. A planilha fornecida não possui uma coluna correspondente às duas **Guitarra 22**, portanto esses dois produtos são criados no cadastro, mas ficam sinalizados com composição pendente para preenchimento no Organiza — sem inventar quantidades.

O **iPhone 17 Premium** utiliza as quantidades estruturais da coluna de iPhone 17 disponível na planilha, trocando somente os componentes de áudio JBL pelos correspondentes Premium; os custos continuam vindo dos Itens do Organiza.

## Itens opcionais ausentes no banco atual

Quando um Item necessário para um Opcional não existe, o Organiza o cria com custo inicial **R$ 0,00** para que o custo correto seja informado em **Cadastros → Itens**. Isso evita inventar valores e mantém o cadastro de Itens como fonte oficial do custo.
