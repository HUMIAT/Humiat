import os

ORGANIZA_VERSAO = "8.15"
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "https://humiat.com.br").rstrip("/")

# Render/Neon: configure DATABASE_URL nas variáveis de ambiente.
# Local: se DATABASE_URL não existir, usa SQLite em organiza.db.
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./organiza.db").strip()
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

CHAVE_SESSAO = os.getenv("SECRET_KEY", "troque-esta-chave-no-render")

ADMIN_NOME = (os.getenv("ORGANIZA_ADMIN_NOME") or os.getenv("ORGANIZA_ADMIN_USUARIO") or "Admin").strip() or "Admin"
ADMIN_SENHA = os.getenv("ORGANIZA_ADMIN_SENHA") or os.getenv("ORGANIZA_SENHA_PADRAO") or "humiat123"


def usando_sqlite() -> bool:
    return DATABASE_URL.startswith("sqlite")


def usando_postgres() -> bool:
    return DATABASE_URL.startswith("postgresql")

# Token privado para integração LokaFest -> Organiza
LOKAFEST_API_TOKEN = os.getenv("LOKAFEST_API_TOKEN", "").strip()

# Integração privada Organiza <-> SolVoz.
# O mesmo token deve estar configurado nos dois serviços.
SOLVOZ_API_TOKEN = os.getenv("SOLVOZ_API_TOKEN", "").strip()
SOLVOZ_BASE_URL = (
    os.getenv("SOLVOZ_BASE_URL", "").strip()
    or os.getenv("HUMIAT_SOLVOZ_URL", "").strip()
    or "https://www.solvoz.com.br"
).rstrip("/")
SOLVOZ_API_TIMEOUT = max(3.0, float(os.getenv("SOLVOZ_API_TIMEOUT", os.getenv("HUMIAT_SOLVOZ_API_TIMEOUT", "12")) or 12))
