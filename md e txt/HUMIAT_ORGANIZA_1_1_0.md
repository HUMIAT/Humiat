# HUMIAT ORGANIZA 1.1.0

## Recibo por equipamento

Foi adicionada a geração de recibo vinculada ao cliente e ao equipamento.

### Alterações
- novo botão **Recibo** na ficha do cliente, dentro do card do equipamento;
- botão posicionado ao lado de **Contrato de garantia**;
- recibo abre em nova aba;
- dados do cliente e do equipamento são preenchidos automaticamente;
- utiliza o valor recebido do equipamento;
- inclui visual de impressão para salvar/imprimir em PDF.

### Arquivos principais
- `app.py`
- `templates/organiza/cliente_detalhe.html`
- `templates/organiza/equipamento_form.html`
- `templates/organiza/equipamento_recibo.html`

### Versão
A versão oficial do Organiza foi atualizada de **1.0.8** para **1.1.0**.

A numeração segue o padrão definido no projeto:
- correções e pequenas melhorias: `1.0.x`;
- novas funcionalidades relevantes: `1.1.0`, `1.2.0`...;
- grandes mudanças incompatíveis: `2.0.0`.

### Commit / deploy
`Organiza v1.1.0 - recibo por equipamento na ficha do cliente`
