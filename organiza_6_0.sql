-- ======================================================
-- ORGANIZA 6.0 - Migração incremental
-- Não recria banco e não apaga dados existentes.
-- Execute somente se quiser migrar manualmente.
-- O app.py também cria/ajusta essas estruturas no startup.
-- ======================================================

CREATE TABLE IF NOT EXISTS historicos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tarefa_id INTEGER NOT NULL,
    tipo VARCHAR(40) NOT NULL DEFAULT 'evento',
    titulo VARCHAR(160) NOT NULL,
    descricao TEXT,
    usuario VARCHAR(80),
    origem VARCHAR(40),
    criado_em DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(tarefa_id) REFERENCES tarefas(id)
);

CREATE TABLE IF NOT EXISTS anexos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tarefa_id INTEGER NOT NULL,
    nome_arquivo VARCHAR(255) NOT NULL,
    caminho VARCHAR(500) NOT NULL,
    tipo VARCHAR(80),
    observacao TEXT,
    criado_por VARCHAR(80),
    criado_em DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(tarefa_id) REFERENCES tarefas(id)
);

CREATE TABLE IF NOT EXISTS mensagens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tarefa_id INTEGER,
    cliente_id INTEGER,
    canal VARCHAR(30) NOT NULL DEFAULT 'WhatsApp',
    direcao VARCHAR(20) NOT NULL DEFAULT 'saida',
    texto TEXT NOT NULL,
    status VARCHAR(30) NOT NULL DEFAULT 'rascunho',
    enviado_por VARCHAR(80),
    enviado_em DATETIME,
    criado_em DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(tarefa_id) REFERENCES tarefas(id),
    FOREIGN KEY(cliente_id) REFERENCES clientes(id)
);

UPDATE tarefas SET status = 'Para Fazer' WHERE status = 'Aberta';
UPDATE tarefas SET status = 'Aguardando Cliente' WHERE status = 'Aguardando';
UPDATE tarefas SET status = 'Concluída' WHERE status IN ('Pronta', 'Entregue');
