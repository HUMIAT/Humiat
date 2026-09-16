# Organiza 1.1.17 — Tipo da NFS-e simplificado

- A tela não exibe mais o Código de Tributação Nacional ao usuário.
- Nova NFS-e manual pergunta apenas o tipo: **Manutenção** ou **Aluguel**.
- NFS-e criada a partir do módulo de Manutenção já vem como **Manutenção** e não permite trocar o tipo.
- Regras internas:
  - Manutenção -> CTN 01.07.01.
  - Aluguel -> CTN 12.09.03.
- A descrição continua editável e o valor total continua separado dos itens.
- O código 12.09.03 foi confirmado em DANFSe anterior de aluguel da Karaokê RJ.
- O NBS do aluguel ainda não é gravado por suposição: deve ser confirmado uma vez no portal atual antes de automatizá-lo.
- Extensão Organiza Fiscal 1.0.73 incluída no pacote.
