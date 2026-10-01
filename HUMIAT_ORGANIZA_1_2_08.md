# Humiat / Organiza 1.2.08

- Adiciona fila **Usuários novos** no ADM Humiat.
- Considera novo quem possui empresa SolVoz vinculada, não possui Humiat ID e não está nas pendências legadas do LokaFest.
- Criação em um clique: Humiat ID + vínculo da empresa + Organiza/Tarefas rápidas + LokaFest/Usuário + SolVoz/Cliente Catálogo.
- Cria o perfil LokaFest antes da identidade central e reaproveita a operação de forma idempotente em caso de nova tentativa.
- Registra o usuário remoto como conciliado para ele não reaparecer em Pendências Humiat ID.
- Envia e-mail de primeiro acesso com texto próprio para cadastro novo.
- A descoberta dos novos usuários é feita em lote, sem N+1 por cliente.
