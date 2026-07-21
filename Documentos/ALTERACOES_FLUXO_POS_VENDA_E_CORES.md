# Atualização — fluxo de pós-venda e linguagem visual

## Aplicado

- Azul representa pendência/ação da equipe.
- Laranja representa pendência/ação do cliente.
- Verde permanece exclusivo para vendas.
- Agenda agora usa o status real da manutenção em vez do rótulo genérico "ENTREGA".
- Agenda, painel, lista e detalhe da manutenção utilizam a mesma regra de cores.
- Identificação de equipamento preserva o padrão `MAQ X · MODELO` com código técnico abaixo.
- Etapa 5 passou a se chamar **Serviço concluído**.
- Etapa 6 passou a se chamar **Aguardando retirada**.
- Ao concluir o serviço, a tela disponibiliza:
  - PDF público de garantia de 30 dias;
  - mensagem de WhatsApp com o link da garantia;
  - link público para o cliente agendar a retirada.
- Logo Karaokê RJ incluída no certificado de garantia.

## Publicação

```bash
git add .
git commit -m "feat: padroniza agenda, status e pos-venda com garantia"
git push origin main
```
