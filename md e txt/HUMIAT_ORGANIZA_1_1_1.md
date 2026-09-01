# HUMIAT Organiza 1.1.1 — QR Code antes da licença

## Objetivo
Permitir gerar os QR Codes do equipamento antes de possuir o NR HD, sem alterar a rotina existente de **Licença e QR Code**.

## Alterações
- Nova etapa **4 — QR Code** na ficha do equipamento, antes da licença.
- O novo botão **Gerar QR Code** exige:
  - NR da máquina válido;
  - plano;
  - Empresa SolVoz.
- O NR HD não é exigido nessa etapa.
- A geração usa exatamente a mesma URL SolVoz configurada para o equipamento.
- O download `KRJxxxxx_QR.zip` contém:
  - QR do catálogo;
  - QR ID da máquina;
  - `URL_CATALOGO.txt`.
- As alterações preenchidas na ficha são salvas antes da geração.
- A rotina existente **Licença e QR Code** permanece funcionando como antes e continua exigindo NR HD.
- As etapas seguintes foram renumeradas apenas para acomodar a nova etapa.

## Arquivos principais
- `app.py`
- `templates/organiza/equipamento_form.html`

## Versão
`1.1.1`

## Commit / deploy
`Organiza v1.1.1 - gera QR antes da licença sem exigir NR HD`
