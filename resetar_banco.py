import os

DB_PATH = "organiza.db"

if os.path.exists(DB_PATH):
    os.remove(DB_PATH)
    print("Banco antigo removido.")

from app import iniciar_banco
iniciar_banco()
print("Banco novo criado com as tabelas do Organiza 7.1.")
