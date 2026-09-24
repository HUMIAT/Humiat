# Organiza 1.1.43

Correção do fluxo “WhatsApp e Próximo”.

- O Organiza salva o destinatário como PROCESSADO antes de abrir o WhatsApp.
- Depois do commit, a tela principal do Organiza já avança para o próximo cliente.
- O WhatsApp abre em uma aba/janela separada por uma página-ponte do próprio Organiza.
- Foi removido o `fetch` + `setTimeout(120ms)` que ficava pendente enquanto o WhatsApp estava aberto.
- O Organiza não espera envio, retorno nem confirmação do WhatsApp. O envio é responsabilidade do atendente.
- A reserva multiatendente continua protegendo contra dois usuários pegarem o mesmo cliente.
