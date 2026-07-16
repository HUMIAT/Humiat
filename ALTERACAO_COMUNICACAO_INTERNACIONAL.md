# HUMIAT 8.1 — Comunicação internacional

## Entregue

- País, DDI e telefone separados no cadastro interno de clientes.
- Migração automática: clientes existentes recebem BR e DDI 55 sem perda de dados.
- `services/comunicacao.py` como fonte única para normalização, validação, número E.164 e URL do WhatsApp.
- Tabela `historico_comunicacoes`.
- Indicadores persistidos em manutenção: `comunicado`, `ultima_comunicacao_em` e `ultima_comunicacao_tipo`.
- Registro automático nas comunicações de prazo, compra/pausa, conclusão e envios agrupados da Central Operacional.
- URLs operacionais de WhatsApp sem DDI 55 fixo.
- Seletor de país com atualização automática do DDI e exemplo do número.
- Compatibilidade por `cliente.whatsapp_completo()`.

## Arquivos principais

- `app.py`: modelos, migração, rotas e integração.
- `services/comunicacao.py`: regras centrais.
- `templates/organiza/cliente_form.html`: cadastro internacional.
- `templates/organiza/base.html`: comportamento compartilhado do formulário.

## Validação executada

- Compilação Python.
- Importação completa da aplicação.
- Inicialização e migração SQLite.
- Conferência das novas tabelas e colunas.
