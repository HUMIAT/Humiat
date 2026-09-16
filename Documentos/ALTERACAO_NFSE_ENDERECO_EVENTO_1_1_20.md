# Organiza 1.1.20 — endereço do evento igual ao cliente

- NFS-e de Aluguel agora segue a mesma lógica simples já usada no cadastro/NFA-e.
- A opção **Endereço do evento igual ao endereço do cliente** vem marcada por padrão.
- Marcada: o Organiza usa automaticamente CEP, logradouro, número, complemento, bairro, município e UF do endereço principal do cliente/tomador.
- Desmarcada: aparecem os campos para informar um endereço diferente para o evento.
- A escolha técnica "Identificador / Endereço no Brasil" deixa de aparecer no Organiza; para o fluxo atual, o portal recebe Endereço no Brasil.
- Rascunhos antigos que já tinham endereço específico preenchido são preservados como endereço diferente.
- Preparado para, no futuro, o Connect definir automaticamente se o evento usa o endereço do cliente ou um endereço próprio do contrato.
