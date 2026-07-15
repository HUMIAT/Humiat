import hashlib
import hmac
import os
import re
import json
import secrets
import csv
import io
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
import unicodedata
from datetime import date, datetime, time, timedelta
from typing import Optional
from urllib.parse import quote

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import ClientDisconnect
from sqlalchemy import Column, Date, DateTime, ForeignKey, Integer, String, Text, Float, func, or_, inspect, text
from sqlalchemy.orm import Session, relationship, selectinload

from config import ADMIN_NOME, ADMIN_SENHA, CHAVE_SESSAO, ORGANIZA_VERSAO, PUBLIC_BASE_URL
from database import Base, SessionLocal, engine, get_db

app = FastAPI(title="Organiza | Karaokê RJ", version=ORGANIZA_VERSAO)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

PREFIXOS_EQUIPAMENTO = {
    "JUKEBOX": "JUK",
    "MALETA": "MAL",
    "IPHONE": "IPH",
    "FLIPERAMA": "FLIP",
}

def tipo_equipamento_padrao(tipo: str) -> str:
    valor = unicodedata.normalize("NFKD", (tipo or "").upper()).encode("ascii", "ignore").decode("ascii").strip()
    aliases = {"IPHON": "IPHONE", "FLIPER": "FLIPERAMA", "ARCADE": "FLIPERAMA"}
    return aliases.get(valor, valor)

def prefixo_equipamento(tipo: str) -> str:
    return PREFIXOS_EQUIPAMENTO.get(tipo_equipamento_padrao(tipo), "EQP")

def rotulo_maquina(equipamento) -> str:
    """Identificação operacional definida pelo cliente: JUK1, MAL2, IPH1, FLIP3."""
    numero = getattr(equipamento, "numero_maquina_cliente", None)
    prefixo = prefixo_equipamento(getattr(equipamento, "tipo", None))
    return f"{prefixo}{numero}" if numero else f"{prefixo}?"

def descricao_equipamento(equipamento) -> str:
    partes = [rotulo_maquina(equipamento)]
    tipo = (getattr(equipamento, "tipo", None) or "EQUIPAMENTO").strip()
    modelo = (getattr(equipamento, "modelo", None) or "").strip()
    partes.append(tipo + (f" {modelo}" if modelo else ""))
    return " · ".join(partes)

def codigo_tecnico(equipamento) -> str:
    return (getattr(equipamento, "maquina", None) or f"Equipamento #{getattr(equipamento, 'id', '?')}").strip()

templates.env.filters["rotulo_maquina"] = rotulo_maquina
templates.env.filters["descricao_equipamento"] = descricao_equipamento
templates.env.filters["codigo_tecnico"] = codigo_tecnico


STATUS_EQUIPE = {"Aguardando equipamento", "Recebida", "Orçamento em elaboração", "Confirmação pendente", "Em manutenção", "Aguardando peça"}
STATUS_CLIENTE = {"Aguardando aprovação", "Aprovado", "Pronto para retirada", "Retirada agendada"}
STATUS_FINAL = {"Encerrada", "Cancelada"}

def responsabilidade_status(status: str) -> str:
    if status in STATUS_FINAL:
        return "finalizado"
    if status in STATUS_CLIENTE:
        return "cliente"
    return "equipe"

def classe_status(status: str) -> str:
    return f"status-{responsabilidade_status(status)}"

def rotulo_status(status: str) -> str:
    mapa = {
        "Aguardando equipamento": "Entrada agendada",
        "Recebida": "Orçamento",
        "Orçamento em elaboração": "Orçamento",
        "Aguardando aprovação": "Aguardando cliente",
        "Aprovado": "Pagamento ou prazo",
        "Confirmação pendente": "Enviar confirmação",
        "Em manutenção": "Execução do serviço",
        "Aguardando peça": "Execução pausada",
        "Pronto para retirada": "Pronto para retirada",
        "Retirada agendada": "Retirada agendada",
        "Encerrada": "Encerrado",
        "Cancelada": "Cancelado",
    }
    return mapa.get(status, status or "Sem status")

templates.env.filters["classe_status"] = classe_status
templates.env.filters["rotulo_status"] = rotulo_status


class Usuario(Base):
    __tablename__ = "usuarios"
    id = Column(Integer, primary_key=True)
    nome = Column(String(80), unique=True, nullable=False)
    telefone = Column(String(20), nullable=True)
    email = Column(String(140), nullable=True)
    cargo = Column(String(80), nullable=True)
    senha_hash = Column(String(255), nullable=False)
    is_admin = Column(Integer, nullable=False, default=0)
    ativo = Column(Integer, nullable=False, default=1)
    criado_em = Column(DateTime, server_default=func.now())


class Cliente(Base):
    __tablename__ = "clientes"
    id = Column(Integer, primary_key=True)
    nome = Column(String(140), nullable=False)
    telefone = Column(String(20), unique=True, nullable=False)
    empresa = Column(String(140), nullable=True)
    documento = Column(String(30), nullable=True)
    cep = Column(String(20), nullable=True)
    cidade = Column(String(120), nullable=True)
    municipio = Column(String(120), nullable=True)
    estado = Column(String(60), nullable=True)
    bairro = Column(String(120), nullable=True)
    endereco = Column(String(255), nullable=True)
    endereco_numero = Column(String(30), nullable=True)
    complemento = Column(String(120), nullable=True)
    email = Column(String(140), nullable=True)
    pacote = Column(String(30), nullable=True)
    falta_pacote = Column(Integer, nullable=True)
    plano = Column(String(60), nullable=True)
    observacao = Column(Text, nullable=True)
    token_ficha = Column(String(64), nullable=True, unique=True)
    inscricao_estadual = Column(String(30), nullable=True)
    criado_em = Column(DateTime, server_default=func.now())
    equipamentos = relationship("Equipamento", back_populates="cliente", cascade="all, delete-orphan")


class Equipamento(Base):
    __tablename__ = "equipamentos"
    id = Column(Integer, primary_key=True)
    cliente_id = Column(Integer, ForeignKey("clientes.id"), nullable=False)
    tipo = Column(String(80), nullable=True)
    modelo = Column(String(180), nullable=True)
    pacote = Column(String(30), nullable=True)
    falta_pacote = Column(Integer, nullable=True)
    plano = Column(String(60), nullable=True)
    valor = Column(String(30), nullable=True)
    preco_custo = Column(String(40), nullable=True)
    preco_venda = Column(String(40), nullable=True)
    pago = Column(String(30), nullable=True)
    falta = Column(String(30), nullable=True)
    data_compra = Column(Date, nullable=True)
    previsao_entrega = Column(Date, nullable=True)
    maquina = Column(String(120), nullable=True)
    rede_instalada = Column(String(120), nullable=True)
    anydesk = Column(String(120), nullable=True)
    status = Column(String(30), nullable=False, default="Ativo")
    observacao = Column(Text, nullable=True)
    garantia_meses = Column(Integer, nullable=True, default=3)
    numero_serie = Column(String(120), nullable=True)  # legado; mantido vazio
    numero_hd = Column(String(160), nullable=True)
    numero_maquina_cliente = Column(Integer, nullable=True)
    fabricante = Column(String(80), nullable=False, default="KARAOKERJ")
    criado_em = Column(DateTime, server_default=func.now())
    cliente = relationship("Cliente", back_populates="equipamentos")


class TransferenciaEquipamento(Base):
    __tablename__ = "equipamento_transferencias"
    id = Column(Integer, primary_key=True)
    equipamento_id = Column(Integer, ForeignKey("equipamentos.id"), nullable=False)
    cliente_origem_id = Column(Integer, ForeignKey("clientes.id"), nullable=False)
    cliente_destino_id = Column(Integer, ForeignKey("clientes.id"), nullable=False)
    observacao = Column(Text, nullable=True)
    criado_em = Column(DateTime, server_default=func.now())
    equipamento = relationship("Equipamento")
    cliente_origem = relationship("Cliente", foreign_keys=[cliente_origem_id])
    cliente_destino = relationship("Cliente", foreign_keys=[cliente_destino_id])


class Item(Base):
    __tablename__ = "catalogo_itens"
    id = Column(Integer, primary_key=True)
    codigo = Column(String(30), nullable=True)
    nome = Column(String(180), unique=True, nullable=False)
    categoria = Column(String(80), nullable=False, default="Geral")
    preco_custo = Column(Float, nullable=False, default=0)
    preco_venda = Column(Float, nullable=False, default=0)
    ativo = Column(Integer, nullable=False, default=1)
    criado_em = Column(DateTime, server_default=func.now())


class Manutencao(Base):
    __tablename__ = "assistencias"
    id = Column(Integer, primary_key=True)
    cliente_id = Column(Integer, ForeignKey("clientes.id"), nullable=False)
    equipamento_id = Column(Integer, ForeignKey("equipamentos.id"), nullable=False)
    defeito = Column(Text, nullable=False)
    diagnostico = Column(Text, nullable=True)
    status = Column(String(40), nullable=False, default="Recebida")
    entrega_prevista_em = Column(DateTime, nullable=True)
    tipo_atendimento = Column(String(20), nullable=False, default="loja")
    recebido_em = Column(DateTime, nullable=True)
    prazo = Column(String(120), nullable=True)
    pronto_em = Column(DateTime, nullable=True)
    retirada_em = Column(DateTime, nullable=True)
    entregue_em = Column(DateTime, nullable=True)
    confirmacao_prazo_em = Column(DateTime, nullable=True)
    servico_pausado_em = Column(DateTime, nullable=True)
    compra_descricao = Column(Text, nullable=True)
    compra_previsao = Column(String(120), nullable=True)
    compra_comunicada_em = Column(DateTime, nullable=True)
    conclusao_comunicada_em = Column(DateTime, nullable=True)
    observacao = Column(Text, nullable=True)
    criado_em = Column(DateTime, server_default=func.now())
    cliente = relationship("Cliente")
    equipamento = relationship("Equipamento")
    orcamentos = relationship("Orcamento", back_populates="manutencao", cascade="all, delete-orphan")


class Orcamento(Base):
    __tablename__ = "assistencia_orcamentos"
    id = Column(Integer, primary_key=True)
    manutencao_id = Column(Integer, ForeignKey("assistencias.id"), nullable=False)
    versao = Column(Integer, nullable=False, default=1)
    token = Column(String(64), unique=True, nullable=False)
    status = Column(String(40), nullable=False, default="Rascunho")
    observacao = Column(Text, nullable=True)
    desconto = Column(Float, nullable=False, default=0)
    # Quando ativo, o desconto só é concedido se todos os itens opcionais forem aprovados.
    desconto_somente_com_opcionais = Column(Integer, nullable=False, default=0)
    valor_manutencao = Column(Float, nullable=False, default=0)
    forma_pagamento_orcamento = Column(String(80), nullable=True)
    prazo_dias_uteis = Column(Integer, nullable=True)
    aprovado_em = Column(DateTime, nullable=True)
    criado_em = Column(DateTime, server_default=func.now())
    manutencao = relationship("Manutencao", back_populates="orcamentos")
    itens = relationship("OrcamentoItem", back_populates="orcamento", cascade="all, delete-orphan")
    pagamentos = relationship("Pagamento", back_populates="orcamento", cascade="all, delete-orphan")


class OrcamentoItem(Base):
    __tablename__ = "assistencia_orcamento_itens"
    id = Column(Integer, primary_key=True)
    orcamento_id = Column(Integer, ForeignKey("assistencia_orcamentos.id"), nullable=False)
    item_id = Column(Integer, ForeignKey("catalogo_itens.id"), nullable=True)
    descricao = Column(String(220), nullable=False)
    quantidade = Column(Integer, nullable=False, default=1)
    preco_custo = Column(Float, nullable=False, default=0)
    preco_venda = Column(Float, nullable=False, default=0)
    opcional = Column(Integer, nullable=False, default=0)
    aprovado = Column(Integer, nullable=False, default=1)
    orcamento = relationship("Orcamento", back_populates="itens")


class Pagamento(Base):
    __tablename__ = "assistencia_pagamentos"
    id = Column(Integer, primary_key=True)
    orcamento_id = Column(Integer, ForeignKey("assistencia_orcamentos.id"), nullable=False)
    data = Column(Date, nullable=False, default=date.today)
    valor = Column(Float, nullable=False)
    forma = Column(String(40), nullable=True)
    observacao = Column(Text, nullable=True)
    criado_em = Column(DateTime, server_default=func.now())
    orcamento = relationship("Orcamento", back_populates="pagamentos")


def limpar_telefone(valor: str) -> str:
    numero = re.sub(r"\D", "", valor or "")
    if numero.startswith("55") and len(numero) > 11:
        numero = numero[2:]
    return numero


def telefone_valido(valor: str) -> bool:
    numero = limpar_telefone(valor)
    return len(numero) == 11 and numero[2] == "9"


def formatar_telefone(valor: str) -> str:
    numero = limpar_telefone(valor)
    return f"({numero[:2]}) {numero[2:7]}-{numero[7:]}" if len(numero) == 11 else (valor or "")


def formatar_data(valor):
    return valor.strftime("%d/%m/%Y") if valor else "-"


def formatar_datahora(valor):
    return valor.strftime("%d/%m/%Y às %H:%M") if valor else "-"


def formatar_moeda(valor):
    if valor in (None, ""):
        return "R$ 0,00"
    try:
        texto = str(valor).replace("R$", "").strip()
        if "," in texto:
            texto = texto.replace(".", "").replace(",", ".")
        numero = float(texto)
        return f"R$ {numero:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return str(valor)


templates.env.filters["telefone"] = formatar_telefone
templates.env.filters["data_br"] = formatar_data
templates.env.filters["datahora"] = formatar_datahora
templates.env.filters["moeda"] = formatar_moeda


def gerar_hash_senha(senha: str, salt: Optional[str] = None) -> str:
    salt = salt or os.urandom(16).hex()
    digest = hashlib.pbkdf2_hmac("sha256", senha.encode(), salt.encode(), 120_000).hex()
    return f"{salt}${digest}"


def verificar_senha(senha: str, senha_hash: str) -> bool:
    try:
        salt, esperado = senha_hash.split("$", 1)
        atual = gerar_hash_senha(senha, salt).split("$", 1)[1]
        return hmac.compare_digest(atual, esperado)
    except Exception:
        return False


def assinatura(nome: str) -> str:
    return hmac.new(CHAVE_SESSAO.encode(), nome.encode(), hashlib.sha256).hexdigest()


def usuario_cookie(request: Request) -> Optional[str]:
    valor = request.cookies.get("humiat_sessao", "")
    if "." not in valor:
        return None
    nome, sig = valor.rsplit(".", 1)
    return nome if hmac.compare_digest(sig, assinatura(nome)) else None


def usuario_logado(request: Request, db: Session = Depends(get_db)) -> Usuario:
    nome = usuario_cookie(request)
    usuario = db.query(Usuario).filter(Usuario.nome == nome, Usuario.ativo == 1).first() if nome else None
    if not usuario:
        raise HTTPException(status_code=303, headers={"Location": "/area-restrita/login"})
    return usuario


def exigir_admin(usuario: Usuario):
    if not usuario.is_admin:
        raise HTTPException(status_code=403, detail="Acesso restrito ao administrador")


def data_form(valor: str):
    try:
        return datetime.strptime(valor, "%Y-%m-%d").date() if valor else None
    except ValueError:
        return None


def datetime_form(valor: str):
    try:
        return datetime.strptime(valor, "%Y-%m-%dT%H:%M") if valor else None
    except ValueError:
        return None


def etapa_manutencao(m):
    """Única fonte de verdade para a etapa atual da manutenção."""
    if m.status == "Encerrada" or m.entregue_em:
        return 7
    if m.retirada_em or m.pronto_em:
        return 6
    if m.confirmacao_prazo_em:
        return 5
    o = sorted(m.orcamentos, key=lambda x: x.versao)[-1] if m.orcamentos else None
    if o and o.status in ("Aprovado", "Aprovado parcialmente", "Aprovado manualmente"):
        return 4
    if o and o.status in ("Enviado", "Aguardando aprovação"):
        return 3
    if (o and o.itens) or m.recebido_em:
        return 2
    return 1

ETAPAS_MANUTENCAO = {
    1: {"rotulo": "Aguardando equipamento", "titulo": "Entrada", "classe": "status-cliente", "responsavel": "Cliente"},
    2: {"rotulo": "Orçamento pendente", "titulo": "Orçamento", "classe": "status-equipe", "responsavel": "Equipe"},
    3: {"rotulo": "Aguardando aceite", "titulo": "Aceite", "classe": "status-cliente", "responsavel": "Cliente"},
    4: {"rotulo": "Pagamento e prazo", "titulo": "Pagamento e prazo", "classe": "status-cliente", "responsavel": "Cliente"},
    5: {"rotulo": "Execução do serviço", "titulo": "Execução do serviço", "classe": "status-equipe", "responsavel": "Equipe"},
    6: {"rotulo": "Aguardando retirada", "titulo": "Aguardando retirada", "classe": "status-cliente", "responsavel": "Cliente"},
    7: {"rotulo": "Encerrado", "titulo": "Encerrado", "classe": "status-finalizado", "responsavel": "Finalizado"},
}

def info_etapa_manutencao(m):
    return ETAPAS_MANUTENCAO[etapa_manutencao(m)]

def data_etapa_manutencao(m):
    """Retorna a data mais representativa da etapa atual."""
    etapa = etapa_manutencao(m)
    orcamento = sorted(m.orcamentos, key=lambda x: x.versao)[-1] if getattr(m, "orcamentos", None) else None
    if etapa == 1:
        return m.entrega_prevista_em or m.criado_em
    if etapa == 2:
        return m.recebido_em or m.criado_em
    if etapa == 3:
        return (orcamento.criado_em if orcamento else None) or m.recebido_em or m.criado_em
    if etapa == 4:
        return (orcamento.aprovado_em if orcamento else None) or m.recebido_em or m.criado_em
    if etapa == 5:
        return m.confirmacao_prazo_em or (orcamento.aprovado_em if orcamento else None) or m.criado_em
    if etapa == 6:
        return m.retirada_em or m.pronto_em or m.criado_em
    return m.entregue_em or m.criado_em

templates.env.globals["etapa_manutencao"] = etapa_manutencao
templates.env.globals["info_etapa_manutencao"] = info_etapa_manutencao
templates.env.globals["data_etapa_manutencao"] = data_etapa_manutencao
templates.env.globals["ETAPAS_MANUTENCAO"] = ETAPAS_MANUTENCAO


@app.on_event("startup")
def iniciar_banco():
    Base.metadata.create_all(bind=engine)
    # Migração leve para bancos já existentes (SQLite e PostgreSQL)
    insp = inspect(engine)
    if "assistencias" in insp.get_table_names():
        existentes = {c["name"] for c in insp.get_columns("assistencias")}
        tipo_dt = "TIMESTAMP" if engine.dialect.name == "postgresql" else "DATETIME"
        with engine.begin() as conn:
            for coluna in ("entrega_prevista_em", "recebido_em", "entregue_em", "confirmacao_prazo_em", "servico_pausado_em", "compra_comunicada_em", "conclusao_comunicada_em"):
                if coluna not in existentes:
                    conn.execute(text(f"ALTER TABLE assistencias ADD COLUMN {coluna} {tipo_dt}"))
            for coluna in ("compra_descricao", "compra_previsao"):
                if coluna not in existentes:
                    conn.execute(text(f"ALTER TABLE assistencias ADD COLUMN {coluna} TEXT"))
            if "tipo_atendimento" not in existentes:
                conn.execute(text("ALTER TABLE assistencias ADD COLUMN tipo_atendimento VARCHAR(20) NOT NULL DEFAULT 'loja'"))
    if "assistencia_orcamentos" in insp.get_table_names():
        existentes_orcamento = {c["name"] for c in insp.get_columns("assistencia_orcamentos")}
        with engine.begin() as conn:
            if "desconto" not in existentes_orcamento:
                conn.execute(text("ALTER TABLE assistencia_orcamentos ADD COLUMN desconto FLOAT NOT NULL DEFAULT 0"))
            if "desconto_somente_com_opcionais" not in existentes_orcamento:
                conn.execute(text("ALTER TABLE assistencia_orcamentos ADD COLUMN desconto_somente_com_opcionais INTEGER NOT NULL DEFAULT 0"))
            if "valor_manutencao" not in existentes_orcamento:
                conn.execute(text("ALTER TABLE assistencia_orcamentos ADD COLUMN valor_manutencao FLOAT NOT NULL DEFAULT 0"))
            if "forma_pagamento_orcamento" not in existentes_orcamento:
                conn.execute(text("ALTER TABLE assistencia_orcamentos ADD COLUMN forma_pagamento_orcamento VARCHAR(80)"))
            if "prazo_dias_uteis" not in existentes_orcamento:
                conn.execute(text("ALTER TABLE assistencia_orcamentos ADD COLUMN prazo_dias_uteis INTEGER"))
    if "clientes" in insp.get_table_names():
        existentes_clientes = {c["name"] for c in insp.get_columns("clientes")}
        with engine.begin() as conn:
            if "inscricao_estadual" not in existentes_clientes:
                conn.execute(text("ALTER TABLE clientes ADD COLUMN inscricao_estadual VARCHAR(30)"))
    if "equipamentos" in insp.get_table_names():
        existentes_equipamentos = {c["name"] for c in insp.get_columns("equipamentos")}
        with engine.begin() as conn:
            if "garantia_meses" not in existentes_equipamentos:
                conn.execute(text("ALTER TABLE equipamentos ADD COLUMN garantia_meses INTEGER DEFAULT 3"))
            if "numero_serie" not in existentes_equipamentos:
                conn.execute(text("ALTER TABLE equipamentos ADD COLUMN numero_serie VARCHAR(120)"))
            if "fabricante" not in existentes_equipamentos:
                conn.execute(text("ALTER TABLE equipamentos ADD COLUMN fabricante VARCHAR(80) DEFAULT 'KARAOKERJ'"))
            if "numero_hd" not in existentes_equipamentos:
                conn.execute(text("ALTER TABLE equipamentos ADD COLUMN numero_hd VARCHAR(160)"))
            if "numero_maquina_cliente" not in existentes_equipamentos:
                conn.execute(text("ALTER TABLE equipamentos ADD COLUMN numero_maquina_cliente INTEGER"))
    db = SessionLocal()
    try:
        if not db.query(Usuario).filter(Usuario.nome == ADMIN_NOME).first():
            db.add(Usuario(nome=ADMIN_NOME, senha_hash=gerar_hash_senha(ADMIN_SENHA), is_admin=1, ativo=1, cargo="Administrador"))
            db.commit()
        if db.query(Item).count() == 0:
            caminho = os.path.join(os.path.dirname(__file__), "itens_seed.json")
            if os.path.exists(caminho):
                with open(caminho, "r", encoding="utf-8") as arquivo:
                    for dado in json.load(arquivo):
                        db.add(Item(**dado, categoria="Geral", ativo=1))
                db.commit()
        # Remove prefixos antigos usados no código do WhatsApp e mantém somente o nome real.
        for cliente_existente in db.query(Cliente).all():
            cliente_existente.nome = limpar_nome_cliente(cliente_existente.nome)

        # Migra o antigo item "Manutenção" para o campo fixo do orçamento.
        for orcamento_existente in db.query(Orcamento).options(selectinload(Orcamento.itens)).all():
            itens_manutencao = [
                item for item in orcamento_existente.itens
                if re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", item.descricao or "").encode("ascii", "ignore").decode("ascii").lower()) == "manutencao"
            ]
            if itens_manutencao:
                if not orcamento_existente.valor_manutencao:
                    orcamento_existente.valor_manutencao = sum(item.preco_venda * item.quantidade for item in itens_manutencao)
                for item in itens_manutencao:
                    db.delete(item)

        # Códigos KRJ existentes são permanentes e nunca são renumerados automaticamente.
        # Apenas completa fabricante e identificações vazias, preservando todo o histórico.
        for equipamento_existente in db.query(Equipamento).all():
            equipamento_existente.fabricante = equipamento_existente.fabricante or "KARAOKERJ"
        db.commit()
    finally:
        db.close()


@app.get("/", response_class=HTMLResponse)
def inicio_publico(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/saude")
def saude():
    return {"status": "ok", "versao": ORGANIZA_VERSAO}


@app.get("/area-restrita/login", response_class=HTMLResponse)
def login(request: Request, erro: str = ""):
    return templates.TemplateResponse("organiza/login.html", {"request": request, "erro": erro})


@app.post("/area-restrita/login")
def entrar(usuario: str = Form(...), senha: str = Form(...), db: Session = Depends(get_db)):
    encontrado = db.query(Usuario).filter(Usuario.nome == usuario.strip(), Usuario.ativo == 1).first()
    if not encontrado or not verificar_senha(senha, encontrado.senha_hash):
        return RedirectResponse("/area-restrita/login?erro=Usuário ou senha inválidos", status_code=303)
    resposta = RedirectResponse("/organiza", status_code=303)
    resposta.set_cookie("humiat_sessao", f"{encontrado.nome}.{assinatura(encontrado.nome)}", httponly=True, samesite="lax", max_age=60 * 60 * 24 * 14)
    return resposta


@app.get("/area-restrita/sair")
def sair():
    resposta = RedirectResponse("/area-restrita/login", status_code=303)
    resposta.delete_cookie("humiat_sessao")
    return resposta


@app.get("/organiza", response_class=HTMLResponse)
def painel(request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    manutencoes = db.query(Manutencao).options(
        selectinload(Manutencao.cliente),
        selectinload(Manutencao.equipamento),
        selectinload(Manutencao.orcamentos).selectinload(Orcamento.itens),
        selectinload(Manutencao.orcamentos).selectinload(Orcamento.pagamentos),
    ).order_by(Manutencao.criado_em.desc()).all()

    pendencias = {
        "entrada": [],
        "orcamento": [],
        "aceite": [],
        "pagamento": [],
        "producao": [],
        "agenda": [],
        "retirada": [],
    }
    chave_por_etapa = {
        1: "entrada",
        2: "orcamento",
        3: "aceite",
        4: "pagamento",
        5: "producao",
        6: "agenda",
    }

    for manutencao in manutencoes:
        etapa = etapa_manutencao(manutencao)
        if etapa == 7:
            continue
        orcamento = sorted(manutencao.orcamentos, key=lambda x: x.versao)[-1] if manutencao.orcamentos else None
        totais = totais_orcamento(orcamento) if orcamento else {"falta": 0}
        manutencao.painel_saldo = totais.get("falta", 0)
        manutencao.painel_etapa = etapa
        manutencao.painel_info = ETAPAS_MANUTENCAO[etapa]

        # Etapa 6 é dividida apenas para facilitar a operação:
        # sem horário = agendamento pendente; com horário = retirada agendada.
        if etapa == 6 and manutencao.retirada_em:
            pendencias["retirada"].append(manutencao)
        else:
            pendencias[chave_por_etapa[etapa]].append(manutencao)

    return templates.TemplateResponse("organiza/painel.html", {
        "request": request,
        "usuario": usuario,
        "total_manutencoes": len(manutencoes),
        "pendencias": pendencias,
        "total_pendencias": sum(len(lista) for lista in pendencias.values()),
    })


@app.get("/organiza/clientes", response_class=HTMLResponse)
def clientes(request: Request, busca: str = "", usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    query = db.query(Cliente).options(selectinload(Cliente.equipamentos))
    termo = busca.strip()
    if termo:
        like = f"%{termo}%"
        query = query.filter(or_(Cliente.nome.ilike(like), Cliente.telefone.ilike(like), Cliente.empresa.ilike(like), Cliente.municipio.ilike(like), Cliente.cidade.ilike(like)))
    lista = query.order_by(Cliente.nome.asc()).all()
    return templates.TemplateResponse("organiza/clientes.html", {
        "request": request, "usuario": usuario, "clientes": lista, "busca": busca,
        "total_clientes": db.query(Cliente).count(), "total_equipamentos": db.query(Equipamento).count(),
    })


def limpar_nome_cliente(nome: str) -> str:
    nome = (nome or "").strip()
    if "_" in nome:
        parte = re.split(r"_+", nome)[-1].strip()
        if parte:
            nome = parte
    return re.sub(r"\s+", " ", nome).strip()


def limpar_documento(documento: str) -> str:
    return re.sub(r"\D", "", documento or "")


def cpf_valido(documento: str) -> bool:
    cpf = limpar_documento(documento)
    if len(cpf) != 11 or cpf == cpf[0] * 11:
        return False
    for tamanho in (9, 10):
        soma = sum(int(cpf[i]) * (tamanho + 1 - i) for i in range(tamanho))
        digito = (soma * 10) % 11
        if digito == 10:
            digito = 0
        if digito != int(cpf[tamanho]):
            return False
    return True


def preencher_cliente(cliente: Cliente, form: dict):
    cliente.nome = limpar_nome_cliente(form.get("nome") or "")
    cliente.telefone = limpar_telefone(form.get("telefone") or "")
    cliente.empresa = (form.get("empresa") or "").strip() or None
    cliente.documento = (form.get("documento") or "").strip() or None
    cliente.inscricao_estadual = (form.get("inscricao_estadual") or "").strip() or None
    cliente.cep = (form.get("cep") or "").strip() or None
    cliente.municipio = (form.get("municipio") or "").strip() or None
    cliente.cidade = cliente.municipio
    cliente.estado = (form.get("estado") or "").strip() or None
    cliente.bairro = (form.get("bairro") or "").strip() or None
    cliente.endereco = (form.get("endereco") or "").strip() or None
    cliente.endereco_numero = (form.get("endereco_numero") or "").strip() or None
    cliente.complemento = (form.get("complemento") or "").strip() or None
    cliente.email = (form.get("email") or "").strip() or None
    cliente.observacao = (form.get("observacao") or "").strip() or None


@app.get("/organiza/clientes/novo", response_class=HTMLResponse)
def cliente_novo(request: Request, usuario: Usuario = Depends(usuario_logado)):
    return templates.TemplateResponse("organiza/cliente_form.html", {"request": request, "usuario": usuario, "cliente": None, "erro": ""})


@app.post("/organiza/clientes/novo")
async def cliente_criar(request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    form = dict(await request.form())
    telefone = limpar_telefone(form.get("telefone") or "")
    erro = ""
    if not (form.get("nome") or "").strip():
        erro = "Informe o nome do cliente."
    elif not telefone_valido(telefone):
        erro = "Informe um celular válido com 11 dígitos, incluindo DDD."
    elif db.query(Cliente).filter(Cliente.telefone == telefone).first():
        erro = "Já existe um cliente com este telefone."
    if erro:
        cliente = Cliente()
        preencher_cliente(cliente, form)
        return templates.TemplateResponse("organiza/cliente_form.html", {"request": request, "usuario": usuario, "cliente": cliente, "erro": erro}, status_code=400)
    cliente = Cliente()
    preencher_cliente(cliente, form)
    db.add(cliente); db.commit(); db.refresh(cliente)
    return RedirectResponse(f"/organiza/clientes/{cliente.id}", status_code=303)


@app.get("/organiza/clientes/{cliente_id}", response_class=HTMLResponse)
def cliente_detalhe(cliente_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    cliente = db.query(Cliente).options(selectinload(Cliente.equipamentos)).filter(Cliente.id == cliente_id).first()
    if not cliente: raise HTTPException(404)
    if not cliente.token_ficha:
        cliente.token_ficha = secrets.token_urlsafe(24)
        db.commit()
    status_filtro = (request.query_params.get("status_equipamento") or "Ativo").strip()
    tipo_filtro = tipo_equipamento_padrao(request.query_params.get("tipo_equipamento") or "")
    equipamentos = list(cliente.equipamentos)
    if status_filtro != "Todos":
        equipamentos = [eq for eq in equipamentos if (eq.status or "Ativo") == status_filtro]
    if tipo_filtro:
        equipamentos = [eq for eq in equipamentos if tipo_equipamento_padrao(eq.tipo or "") == tipo_filtro]
    equipamentos = ordenar_equipamentos(equipamentos)
    manutencoes = db.query(Manutencao).filter(Manutencao.cliente_id == cliente_id).order_by(Manutencao.criado_em.desc()).all()
    return templates.TemplateResponse("organiza/cliente_detalhe.html", {
        "request": request, "usuario": usuario, "cliente": cliente, "manutencoes": manutencoes,
        "equipamentos": equipamentos, "status_filtro": status_filtro, "tipo_filtro": tipo_filtro,
    })


@app.get("/organiza/clientes/{cliente_id}/editar", response_class=HTMLResponse)
def cliente_editar(cliente_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    cliente = db.query(Cliente).filter(Cliente.id == cliente_id).first()
    if not cliente: raise HTTPException(404)
    return templates.TemplateResponse("organiza/cliente_form.html", {"request": request, "usuario": usuario, "cliente": cliente, "erro": ""})


@app.post("/organiza/clientes/{cliente_id}/editar")
async def cliente_salvar(cliente_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    cliente = db.query(Cliente).filter(Cliente.id == cliente_id).first()
    if not cliente: raise HTTPException(404)
    form = dict(await request.form())
    telefone = limpar_telefone(form.get("telefone") or "")
    erro = ""
    if not (form.get("nome") or "").strip(): erro = "Informe o nome do cliente."
    elif not telefone_valido(telefone): erro = "Informe um celular válido com 11 dígitos, incluindo DDD."
    elif db.query(Cliente).filter(Cliente.telefone == telefone, Cliente.id != cliente_id).first(): erro = "Já existe outro cliente com este telefone."
    if erro:
        preencher_cliente(cliente, form)
        return templates.TemplateResponse("organiza/cliente_form.html", {"request": request, "usuario": usuario, "cliente": cliente, "erro": erro}, status_code=400)
    preencher_cliente(cliente, form); db.commit()
    return RedirectResponse(f"/organiza/clientes/{cliente.id}", status_code=303)


def preencher_equipamento(eq: Equipamento, form: dict):
    eq.tipo = tipo_equipamento_padrao((form.get("tipo") or "").strip()) or None
    eq.modelo = (form.get("modelo") or "").strip() or None
    eq.pacote = (form.get("pacote") or "").strip() or None
    try: eq.falta_pacote = int(form.get("falta_pacote")) if form.get("falta_pacote") else None
    except ValueError: eq.falta_pacote = None
    eq.plano = (form.get("plano") or "").strip() or None
    eq.valor = (form.get("valor") or "").strip() or None
    eq.preco_custo = (form.get("preco_custo") or "").strip() or None
    eq.preco_venda = (form.get("preco_venda") or "").strip() or None
    eq.pago = (form.get("pago") or "").strip() or None
    # O saldo é sempre calculado no servidor para não depender do navegador.
    total = moeda_num(eq.valor)
    recebido = moeda_num(eq.pago)
    eq.falta = f"{max(total - recebido, 0):.2f}" if (eq.valor or eq.pago) else None
    eq.data_compra = data_form(form.get("data_compra") or "")
    eq.previsao_entrega = data_form(form.get("previsao_entrega") or "")
    # Identificadores podem ser corrigidos sem alterar os vínculos históricos.
    maquina_informada = re.sub(r"[^A-Z0-9]", "", (form.get("maquina") or "").strip().upper())
    if maquina_informada:
        eq.maquina = maquina_informada
    try:
        numero_cliente = int(form.get("numero_maquina_cliente") or 0)
        if numero_cliente > 0:
            eq.numero_maquina_cliente = numero_cliente
    except (TypeError, ValueError):
        pass
    eq.numero_hd = re.sub(r"[^A-Z0-9]", "", (form.get("numero_hd") or "").strip().upper()) or None
    eq.numero_serie = None
    eq.status = (form.get("status") or "Ativo").strip()
    eq.observacao = (form.get("observacao") or "").strip() or None
    fabricante = (form.get("fabricante") or "KARAOKERJ").strip().upper()
    eq.fabricante = fabricante if fabricante in ("KARAOKERJ", "OUTROS") else "KARAOKERJ"
    try:
        eq.garantia_meses = max(int(form.get("garantia_meses") or 3), 0)
    except ValueError:
        eq.garantia_meses = 3


def _chave_ordenacao_equipamento(equipamento: Equipamento):
    """Ordena pela entrega; registros sem entrega ficam depois, mantendo ordem estável."""
    data_referencia = equipamento.previsao_entrega or equipamento.data_compra
    data_ordem = data_referencia.toordinal() if data_referencia else 9999999
    criado_ordem = equipamento.criado_em.timestamp() if equipamento.criado_em else 0
    return data_ordem, criado_ordem, equipamento.id or 0


def _chave_ordenacao_equipamento(equipamento: Equipamento):
    """Tipo, número do cliente e data de entrega; usada em todas as listas."""
    ordem_tipo = {"JUKEBOX": 1, "MALETA": 2, "IPHONE": 3, "FLIPERAMA": 4}
    tipo = tipo_equipamento_padrao(equipamento.tipo or "")
    numero = equipamento.numero_maquina_cliente or 999999
    data_referencia = equipamento.previsao_entrega or equipamento.data_compra or date.max
    return ordem_tipo.get(tipo, 99), numero, data_referencia, equipamento.id or 0


def ordenar_equipamentos(equipamentos):
    return sorted(equipamentos, key=_chave_ordenacao_equipamento)


def proximo_codigo_maquina(db: Session) -> str:
    """Cria o próximo KRJ livre sem alterar códigos já cadastrados."""
    usados = {
        int(m.group(1))
        for (codigo,) in db.query(Equipamento.maquina).filter(Equipamento.maquina.isnot(None)).all()
        if (m := re.fullmatch(r"KRJ(\d{5})", (codigo or "").strip().upper()))
    }
    numero = 41
    while numero in usados:
        numero += 1
    return f"KRJ{numero:05d}"


def proximo_numero_cliente(db: Session, cliente_id: int, tipo: str = "JUKEBOX") -> int:
    tipo = tipo_equipamento_padrao(tipo)
    numeros = [
        n for (n,) in db.query(Equipamento.numero_maquina_cliente)
        .filter(Equipamento.cliente_id == cliente_id, func.upper(Equipamento.tipo) == tipo)
        .all() if n
    ]
    return (max(numeros) if numeros else 0) + 1




def reordenar_series_cliente(db: Session, cliente_id: int):
    """Mantido por compatibilidade. A identificação do cliente não é mais renumerada automaticamente."""
    return None

def garantir_identificacao_equipamento(db: Session, equipamento: Equipamento):
    equipamento.tipo = tipo_equipamento_padrao(equipamento.tipo or "")
    equipamento.fabricante = equipamento.fabricante or "KARAOKERJ"
    if not equipamento.maquina:
        equipamento.maquina = proximo_codigo_maquina(db)
    if not equipamento.numero_maquina_cliente:
        equipamento.numero_maquina_cliente = proximo_numero_cliente(db, equipamento.cliente_id, equipamento.tipo)


def validar_codigo_monitor(db: Session, equipamento: Equipamento, codigo_anterior: str = "") -> str:
    codigo = (equipamento.maquina or "").strip().upper()
    if not re.fullmatch(r"KRJ\d{5}", codigo):
        return "O campo “Código da máquina para o monitor” deve seguir o padrão KRJ00001."
    duplicado = db.query(Equipamento).filter(
        func.upper(Equipamento.maquina) == codigo,
        Equipamento.id != (equipamento.id or 0)
    ).first()
    if duplicado:
        return f"O código do monitor {codigo} já está sendo utilizado e não pode ser repetido."
    anterior = (codigo_anterior or "").strip().upper()
    if anterior and codigo != anterior:
        numero_anterior = int(anterior[3:]) if re.fullmatch(r"KRJ\d{5}", anterior) else 99999
        if not 1 <= numero_anterior <= 40:
            return f"O código do monitor {anterior} é permanente. Somente códigos de KRJ00001 a KRJ00040 podem ser corrigidos."
    return ""


def equipamento_codigo_duplicado(db: Session, equipamento: Equipamento):
    return db.query(Equipamento).filter(
        Equipamento.cliente_id == equipamento.cliente_id,
        func.upper(Equipamento.tipo) == tipo_equipamento_padrao(equipamento.tipo or ""),
        Equipamento.numero_maquina_cliente == equipamento.numero_maquina_cliente,
        Equipamento.id != (equipamento.id or 0)
    ).first()


def opcoes_equipamentos(db: Session):
    tipos_bd = [x[0] for x in db.query(Equipamento.tipo).filter(Equipamento.tipo.isnot(None), Equipamento.tipo != "").distinct().order_by(Equipamento.tipo).all()]
    pacotes_bd = [x[0] for x in db.query(Equipamento.pacote).filter(Equipamento.pacote.isnot(None), Equipamento.pacote != "").distinct().order_by(Equipamento.pacote.desc()).all()]
    tipos = list(dict.fromkeys(["JUKEBOX", "MALETA", "IPHONE", "FLIPERAMA"] + tipos_bd))
    pacotes = list(dict.fromkeys(["2026.2", "2026.1"] + pacotes_bd))
    return tipos, pacotes


@app.get("/organiza/clientes/{cliente_id}/equipamentos/novo", response_class=HTMLResponse)
def equipamento_novo(cliente_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    cliente = db.query(Cliente).filter(Cliente.id == cliente_id).first()
    if not cliente: raise HTTPException(404)
    tipos, pacotes = opcoes_equipamentos(db)
    return templates.TemplateResponse("organiza/equipamento_form.html", {
        "request": request, "usuario": usuario, "cliente": cliente, "equipamento": None,
        "erro": "", "tipos": tipos, "pacotes": pacotes,
        "proxima_maquina": proximo_codigo_maquina(db),
        "proximo_numero_cliente": proximo_numero_cliente(db, cliente_id),
    })


@app.post("/organiza/clientes/{cliente_id}/equipamentos/novo")
async def equipamento_criar(cliente_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    cliente = db.query(Cliente).filter(Cliente.id == cliente_id).first()
    if not cliente: raise HTTPException(404)
    form = dict(await request.form())
    if not (form.get("tipo") or "").strip():
        eq = Equipamento(cliente_id=cliente_id); preencher_equipamento(eq, form)
        tipos, pacotes = opcoes_equipamentos(db)
        return templates.TemplateResponse("organiza/equipamento_form.html", {
            "request": request, "usuario": usuario, "cliente": cliente, "equipamento": eq,
            "erro": "Informe o tipo do equipamento.", "tipos": tipos, "pacotes": pacotes,
            "proxima_maquina": proximo_codigo_maquina(db),
            "proximo_numero_cliente": proximo_numero_cliente(db, cliente_id),
        }, status_code=400)
    eq = Equipamento(cliente_id=cliente_id); preencher_equipamento(eq, form)
    garantir_identificacao_equipamento(db, eq)
    erro_identificacao = validar_codigo_monitor(db, eq)
    duplicado_cliente = equipamento_codigo_duplicado(db, eq)
    confirmou_duplicado = form.get("confirmar_codigo_cliente") == "1"
    if duplicado_cliente and not confirmou_duplicado:
        erro_identificacao = (
            f"Este cliente já possui {rotulo_maquina(eq)}. "
            "Confirme abaixo para utilizar o mesmo código; depois altere ou exclua o cadastro antigo."
        )
    if erro_identificacao:
        tipos, pacotes = opcoes_equipamentos(db)
        return templates.TemplateResponse("organiza/equipamento_form.html", {
            "request": request, "usuario": usuario, "cliente": cliente, "equipamento": eq,
            "erro": erro_identificacao, "tipos": tipos, "pacotes": pacotes,
            "proxima_maquina": eq.maquina,
            "proximo_numero_cliente": eq.numero_maquina_cliente,
            "confirmar_duplicado": bool(duplicado_cliente and not confirmou_duplicado),
        }, status_code=400)
    db.add(eq)
    db.commit()
    return RedirectResponse(f"/organiza/clientes/{cliente_id}", status_code=303)


@app.get("/organiza/clientes/{cliente_id}/equipamentos/{equipamento_id}/editar", response_class=HTMLResponse)
def equipamento_editar(cliente_id: int, equipamento_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    cliente = db.query(Cliente).filter(Cliente.id == cliente_id).first()
    eq = db.query(Equipamento).filter(Equipamento.id == equipamento_id, Equipamento.cliente_id == cliente_id).first()
    if not cliente or not eq: raise HTTPException(404)
    tipos, pacotes = opcoes_equipamentos(db)
    clientes_transferencia = db.query(Cliente).filter(Cliente.id != cliente_id).order_by(Cliente.nome.asc()).all()
    transferencias = db.query(TransferenciaEquipamento).filter(TransferenciaEquipamento.equipamento_id == equipamento_id).order_by(TransferenciaEquipamento.criado_em.desc()).all()
    return templates.TemplateResponse("organiza/equipamento_form.html", {"request": request, "usuario": usuario, "cliente": cliente, "equipamento": eq, "erro": "", "tipos": tipos, "pacotes": pacotes, "clientes_transferencia": clientes_transferencia, "transferencias": transferencias})


@app.post("/organiza/clientes/{cliente_id}/equipamentos/{equipamento_id}/editar")
async def equipamento_salvar(cliente_id: int, equipamento_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    eq = db.query(Equipamento).filter(Equipamento.id == equipamento_id, Equipamento.cliente_id == cliente_id).first()
    if not eq: raise HTTPException(404)
    form = dict(await request.form())
    codigo_anterior = (eq.maquina or "").strip().upper()
    preencher_equipamento(eq, form)
    garantir_identificacao_equipamento(db, eq)
    erro_identificacao = validar_codigo_monitor(db, eq, codigo_anterior)
    duplicado_cliente = equipamento_codigo_duplicado(db, eq)
    confirmou_duplicado = form.get("confirmar_codigo_cliente") == "1"
    if duplicado_cliente and not confirmou_duplicado:
        erro_identificacao = (
            f"Este cliente já possui {rotulo_maquina(eq)}. "
            "Confirme abaixo para utilizar o mesmo código; depois altere ou exclua o cadastro antigo."
        )
    if erro_identificacao:
        cliente = db.query(Cliente).filter(Cliente.id == cliente_id).first()
        tipos, pacotes = opcoes_equipamentos(db)
        clientes_transferencia = db.query(Cliente).filter(Cliente.id != cliente_id).order_by(Cliente.nome.asc()).all()
        transferencias = db.query(TransferenciaEquipamento).filter(TransferenciaEquipamento.equipamento_id == equipamento_id).order_by(TransferenciaEquipamento.criado_em.desc()).all()
        return templates.TemplateResponse("organiza/equipamento_form.html", {
            "request": request, "usuario": usuario, "cliente": cliente, "equipamento": eq,
            "erro": erro_identificacao, "tipos": tipos, "pacotes": pacotes,
            "clientes_transferencia": clientes_transferencia, "transferencias": transferencias,
            "confirmar_duplicado": bool(duplicado_cliente and not confirmou_duplicado)
        }, status_code=400)
    db.commit()
    return RedirectResponse(f"/organiza/clientes/{cliente_id}", status_code=303)



PASTA_LICENCAS = Path(os.getenv("PASTA_LICENCAS", Path(__file__).resolve().parent / "licencas_geradas"))


def normalizar_plano_qr(plano: Optional[str]) -> str:
    valor = unicodedata.normalize("NFKD", (plano or "PLUS").upper()).encode("ascii", "ignore").decode()
    return "BASICO" if valor == "BASICO" else "PLUS"


def validar_dados_licenca(equipamento: Equipamento):
    maquina = re.sub(r"[^A-Z0-9]", "", (equipamento.maquina or "").upper())
    numero_hd = re.sub(r"[^A-Z0-9]", "", (equipamento.numero_hd or "").upper())
    if not re.fullmatch(r"KRJ\d{5}", maquina):
        raise HTTPException(400, "O número da máquina deve seguir o padrão KRJ00040.")
    if not numero_hd.startswith("KRJHD"):
        raise HTTPException(400, "O campo NR HD deve conter o código completo iniciado por KRJHD.")
    return maquina, numero_hd, normalizar_plano_qr(equipamento.plano)


def criar_arte_qr(maquina: str, plano: str, destino: Path):
    """Reproduz a arte do gerador AU3: 900x1100, textos e QR centralizado."""
    try:
        import qrcode
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:
        raise HTTPException(
            500,
            "Dependências do QR ausentes. Execute: pip install qrcode[pil] Pillow"
        ) from exc

    url = f"https://www.karaokerj.com.br/catalogo?m={maquina}&plano={plano.lower()}"
    qr = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=12, border=4)
    qr.add_data(url)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="black", back_color="white").convert("RGB").resize((620, 620))

    arte = Image.new("RGB", (900, 1100), "white")
    arte.paste(qr_img, (140, 410))
    desenho = ImageDraw.Draw(arte)

    def fonte(tamanho: int, negrito: bool = False):
        candidatos = [
            "C:/Windows/Fonts/arialbd.ttf" if negrito else "C:/Windows/Fonts/arial.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if negrito else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ]
        for caminho in candidatos:
            if Path(caminho).exists():
                return ImageFont.truetype(caminho, tamanho)
        return ImageFont.load_default()

    def central(texto: str, y: int, tamanho: int, negrito: bool = True):
        f = fonte(tamanho, negrito)
        caixa = desenho.textbbox((0, 0), texto, font=f)
        x = (900 - (caixa[2] - caixa[0])) // 2
        desenho.text((x, y), texto, fill="black", font=f)

    central("Escaneie para enviar musicas", 70, 42)
    central("Equipamento:", 190, 34)
    central(maquina, 255, 50)
    central(f"Catalogo: {plano}", 335, 30)
    arte.save(destino, "PNG")


def gerar_pasta_licenca(equipamento: Equipamento) -> tuple[Path, Path]:
    maquina, numero_hd, plano = validar_dados_licenca(equipamento)
    pasta = PASTA_LICENCAS / maquina
    pasta.mkdir(parents=True, exist_ok=True)

    (pasta / "MAQUINA_KRJ.txt").write_text(maquina, encoding="utf-8")
    (pasta / "LICENCA_HD_KRJ.txt").write_text(numero_hd, encoding="utf-8")
    png = pasta / f"QR_{maquina}_{plano}.png"
    criar_arte_qr(maquina, plano, png)

    zip_path = PASTA_LICENCAS / f"{maquina}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as pacote:
        for arquivo in [pasta / "MAQUINA_KRJ.txt", pasta / "LICENCA_HD_KRJ.txt", png]:
            pacote.write(arquivo, arcname=f"{maquina}/{arquivo.name}")
    return pasta, zip_path


@app.post("/organiza/clientes/{cliente_id}/equipamentos/{equipamento_id}/gerar-licenca")
async def equipamento_gerar_licenca(cliente_id: int, equipamento_id: int, request: Request,
                                    usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    eq = db.query(Equipamento).filter(
        Equipamento.id == equipamento_id,
        Equipamento.cliente_id == cliente_id
    ).first()
    if not eq:
        raise HTTPException(404)

    # O mesmo botão salva as correções feitas nos campos antes de gerar.
    form = dict(await request.form())
    preencher_equipamento(eq, form)
    garantir_identificacao_equipamento(db, eq)
    db.commit()
    _, zip_path = gerar_pasta_licenca(eq)
    return FileResponse(
        path=zip_path,
        media_type="application/zip",
        filename=zip_path.name,
        headers={"X-Pasta-Gerada": str(PASTA_LICENCAS / (eq.maquina or ""))}
    )


@app.post("/organiza/clientes/{cliente_id}/equipamentos/{equipamento_id}/transferir")
async def equipamento_transferir(cliente_id: int, equipamento_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    eq = db.query(Equipamento).filter(Equipamento.id == equipamento_id, Equipamento.cliente_id == cliente_id).first()
    if not eq:
        raise HTTPException(404)
    form = dict(await request.form())
    destino_id = int(form.get("cliente_destino_id") or 0)
    destino = db.query(Cliente).filter(Cliente.id == destino_id).first()
    if not destino or destino_id == cliente_id:
        return RedirectResponse(f"/organiza/clientes/{cliente_id}/equipamentos/{equipamento_id}/editar?erro_transferencia=Selecione outro cliente", status_code=303)
    manutencao_aberta = db.query(Manutencao).filter(
        Manutencao.equipamento_id == equipamento_id,
        Manutencao.entregue_em.is_(None),
        Manutencao.status.notin_(("Encerrada", "Cancelada", "Entregue")),
    ).first()
    if manutencao_aberta:
        return RedirectResponse(f"/organiza/clientes/{cliente_id}/equipamentos/{equipamento_id}/editar?erro_transferencia=Existe manutenção aberta para este equipamento", status_code=303)
    db.add(TransferenciaEquipamento(
        equipamento_id=eq.id, cliente_origem_id=cliente_id, cliente_destino_id=destino_id,
        observacao=(form.get("observacao_transferencia") or "").strip() or None,
    ))
    eq.cliente_id = destino_id
    db.flush()
    reordenar_series_cliente(db, cliente_id)
    reordenar_series_cliente(db, destino_id)
    db.commit()
    return RedirectResponse(f"/organiza/clientes/{destino_id}/equipamentos/{equipamento_id}/editar?transferido=1", status_code=303)


# ---------------------------------------------------------
# VENDAS SIMPLES
# Uma venda cria (ou reutiliza) o cliente e já cadastra o equipamento.
# ---------------------------------------------------------

STATUS_VENDA = ("Solicitar gabinete", "Montagem", "Pronto para entrega", "Entregue")


def equipamento_eh_venda(eq: Equipamento) -> bool:
    return bool(eq.data_compra or eq.previsao_entrega or eq.valor or eq.pago or eq.status in STATUS_VENDA)


@app.get("/organiza/vendas", response_class=HTMLResponse)
def vendas(request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    equipamentos = db.query(Equipamento).options(selectinload(Equipamento.cliente)).order_by(
        Equipamento.previsao_entrega.asc(), Equipamento.criado_em.desc()
    ).all()
    equipamentos = [eq for eq in equipamentos if equipamento_eh_venda(eq)]
    return templates.TemplateResponse("organiza/vendas.html", {
        "request": request, "usuario": usuario, "vendas": equipamentos
    })


@app.get("/organiza/vendas/nova", response_class=HTMLResponse)
def venda_nova(request: Request, cliente_id: int = 0, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    clientes = db.query(Cliente).order_by(Cliente.nome.asc()).all()
    tipos, pacotes = opcoes_equipamentos(db)
    return templates.TemplateResponse("organiza/venda_nova.html", {
        "request": request, "usuario": usuario, "clientes": clientes,
        "cliente_id": cliente_id, "erro": "", "dados": {}, "status_venda": STATUS_VENDA, "tipos": tipos, "pacotes": pacotes
    })


@app.post("/organiza/vendas/nova")
async def venda_criar(request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    form = dict(await request.form())
    tipos, pacotes = opcoes_equipamentos(db)
    clientes = db.query(Cliente).order_by(Cliente.nome.asc()).all()
    cliente = None
    cliente_id = int(form.get("cliente_id") or 0)
    telefone = limpar_telefone(form.get("telefone") or "")
    nome = (form.get("nome") or "").strip()

    if cliente_id:
        cliente = db.query(Cliente).filter(Cliente.id == cliente_id).first()
    elif telefone:
        cliente = db.query(Cliente).filter(Cliente.telefone == telefone).first()
        if not cliente:
            if not nome:
                erro = "Informe o nome para cadastrar o novo cliente."
                return templates.TemplateResponse("organiza/venda_nova.html", {"request": request, "usuario": usuario, "clientes": clientes, "cliente_id": 0, "erro": erro, "dados": form, "status_venda": STATUS_VENDA, "tipos": tipos, "pacotes": pacotes}, status_code=400)
            if not telefone_valido(telefone):
                erro = "Informe um WhatsApp válido com 11 dígitos, incluindo DDD."
                return templates.TemplateResponse("organiza/venda_nova.html", {"request": request, "usuario": usuario, "clientes": clientes, "cliente_id": 0, "erro": erro, "dados": form, "status_venda": STATUS_VENDA, "tipos": tipos, "pacotes": pacotes}, status_code=400)
            cliente = Cliente(nome=nome, telefone=telefone)
            cliente.email = (form.get("email") or "").strip() or None
            cliente.municipio = (form.get("municipio") or "").strip() or None
            cliente.cidade = cliente.municipio
            cliente.observacao = (form.get("cliente_observacao") or "").strip() or None
            db.add(cliente)
            db.flush()

    if not cliente:
        erro = "Selecione um cliente existente ou informe nome e WhatsApp para criar um novo."
        return templates.TemplateResponse("organiza/venda_nova.html", {"request": request, "usuario": usuario, "clientes": clientes, "cliente_id": cliente_id, "erro": erro, "dados": form, "status_venda": STATUS_VENDA, "tipos": tipos, "pacotes": pacotes}, status_code=400)
    if not (form.get("tipo") or "").strip():
        erro = "Informe o tipo do equipamento."
        return templates.TemplateResponse("organiza/venda_nova.html", {"request": request, "usuario": usuario, "clientes": clientes, "cliente_id": cliente.id, "erro": erro, "dados": form, "status_venda": STATUS_VENDA, "tipos": tipos, "pacotes": pacotes}, status_code=400)

    eq = Equipamento(cliente_id=cliente.id)
    preencher_equipamento(eq, form)
    if eq.status not in STATUS_VENDA:
        eq.status = "Solicitar gabinete"
    garantir_identificacao_equipamento(db, eq)
    db.add(eq)
    db.flush()
    reordenar_series_cliente(db, cliente.id)
    db.commit()
    db.refresh(eq)
    return RedirectResponse(f"/organiza/clientes/{cliente.id}/equipamentos/{eq.id}/editar?criado=1", status_code=303)


@app.get("/cadastro/{token}", response_class=HTMLResponse)
def cadastro_publico(token: str, request: Request, db: Session = Depends(get_db)):
    cliente = db.query(Cliente).filter(Cliente.token_ficha == token).first()
    if not cliente:
        raise HTTPException(404)
    return templates.TemplateResponse("organiza/cadastro_publico.html", {"request": request, "cliente": cliente, "erro": "", "salvo": request.query_params.get("salvo")})


@app.post("/cadastro/{token}")
async def cadastro_publico_salvar(token: str, request: Request, db: Session = Depends(get_db)):
    cliente = db.query(Cliente).filter(Cliente.token_ficha == token).first()
    if not cliente:
        raise HTTPException(404)
    form = dict(await request.form())
    telefone_original = cliente.telefone
    preencher_cliente(cliente, form)
    if not cliente.nome or not telefone_valido(cliente.telefone):
        cliente.telefone = telefone_original
        return templates.TemplateResponse("organiza/cadastro_publico.html", {"request": request, "cliente": cliente, "erro": "Informe nome e WhatsApp válidos.", "salvo": False}, status_code=400)
    duplicado = db.query(Cliente).filter(Cliente.telefone == cliente.telefone, Cliente.id != cliente.id).first()
    if duplicado:
        cliente.telefone = telefone_original
        return templates.TemplateResponse("organiza/cadastro_publico.html", {"request": request, "cliente": cliente, "erro": "Este WhatsApp já pertence a outro cadastro.", "salvo": False}, status_code=400)
    db.commit()
    return RedirectResponse(f"/cadastro/{token}?salvo=1", status_code=303)


def _nome_arquivo(texto: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", texto or "cliente").strip("_") or "cliente"


@app.get("/organiza/equipamentos/{equipamento_id}/garantia.pdf")
def garantia_pdf(equipamento_id: int, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    eq = db.query(Equipamento).options(selectinload(Equipamento.cliente)).filter(Equipamento.id == equipamento_id).first()
    if not eq:
        raise HTTPException(404)

    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    fonte_regular = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    fonte_negrito = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    nome_fonte = "DejaVu"
    nome_fonte_bold = "DejaVu-Bold"
    if os.path.exists(fonte_regular) and os.path.exists(fonte_negrito):
        if nome_fonte not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(nome_fonte, fonte_regular))
            pdfmetrics.registerFont(TTFont(nome_fonte_bold, fonte_negrito))
    else:
        nome_fonte, nome_fonte_bold = "Helvetica", "Helvetica-Bold"

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4, rightMargin=15 * mm, leftMargin=15 * mm,
        topMargin=12 * mm, bottomMargin=14 * mm, title="Venda de Equipamentos - Termo de Garantia"
    )
    styles = getSampleStyleSheet()
    corpo = ParagraphStyle("ContratoCorpo", parent=styles["BodyText"], fontName=nome_fonte, fontSize=9.2, leading=13, alignment=TA_JUSTIFY, spaceAfter=7)
    titulo = ParagraphStyle("ContratoTitulo", parent=styles["Title"], fontName=nome_fonte_bold, fontSize=13, leading=16, alignment=TA_CENTER, spaceAfter=12)
    cabecalho = ParagraphStyle("ContratoCabecalho", parent=corpo, fontSize=8.5, leading=11)

    cliente = eq.cliente
    data_entrega = eq.previsao_entrega
    data_texto = data_entrega.strftime("%d de %B de %Y") if data_entrega else "data de entrega não informada"
    meses_pt = {1:"janeiro",2:"fevereiro",3:"março",4:"abril",5:"maio",6:"junho",7:"julho",8:"agosto",9:"setembro",10:"outubro",11:"novembro",12:"dezembro"}
    if data_entrega:
        data_texto = f"{data_entrega.day:02d} de {meses_pt[data_entrega.month]} de {data_entrega.year}"

    endereco_partes = [cliente.endereco, cliente.endereco_numero]
    endereco = ", ".join(str(x).strip() for x in endereco_partes if x)
    if cliente.complemento:
        endereco += (", " if endereco else "") + cliente.complemento
    if cliente.bairro:
        endereco += (" - " if endereco else "") + cliente.bairro
    cidade_uf = " - ".join(x for x in [cliente.municipio or cliente.cidade, cliente.estado] if x)
    if cidade_uf:
        endereco += (", " if endereco else "") + cidade_uf
    endereco = endereco or "endereço não informado"

    equipamento = " ".join(x for x in [eq.tipo, eq.modelo] if x).strip() or "equipamento de karaokê"
    pacote = f", atualizado até o pacote {eq.pacote}" if eq.pacote else ""
    valor = formatar_moeda(eq.valor)
    meses = eq.garantia_meses if eq.garantia_meses is not None else 3

    logo_path = os.path.join(os.path.dirname(__file__), "static", "img", "karaoke-rj-garantia.jpeg")
    logo = Image(logo_path, width=25 * mm, height=25 * mm) if os.path.exists(logo_path) else Spacer(25 * mm, 25 * mm)
    dados_empresa = Paragraph(
        "<b>EMPRESA KARAOKE &amp; GAMES RJ.</b><br/>"
        "CNPJ: 35.458.112/0001-75 &nbsp;&nbsp;&nbsp; IM: 1213508-4<br/>"
        "Rua João Romariz, 313 - Ramos - Rio de Janeiro/RJ - CEP: 21031-700<br/>"
        "WhatsApp: (21) 99507-9690 / (21) 99650-4516<br/>"
        "www.karaokerj.com.br &nbsp;&nbsp; contato@karaokerj.com.br", cabecalho
    )
    header = Table([[logo, dados_empresa]], colWidths=[30 * mm, 145 * mm])
    header.setStyle(TableStyle([("VALIGN", (0,0), (-1,-1), "MIDDLE"), ("LINEBELOW", (0,0), (-1,-1), 0.7, colors.HexColor("#555555")), ("LEFTPADDING", (0,0), (-1,-1), 0), ("RIGHTPADDING", (0,0), (-1,-1), 0), ("BOTTOMPADDING", (0,0), (-1,-1), 5)]))

    story = [header, Spacer(1, 8), Paragraph("VENDA DE EQUIPAMENTOS - TERMO DE GARANTIA", titulo)]
    story.append(Paragraph(
        f"Pelo presente instrumento particular, de um lado <b>KARAOKE &amp; GAMES RJ</b>, inscrita no CNPJ "
        f"35.458.112/0001-75, estabelecida à Rua João Romariz, 313, Fundos, Ramos, Rio de Janeiro/RJ, "
        f"doravante denominada <b>VENDEDORA</b>, e de outro lado <b>{cliente.nome}</b>, CPF/CNPJ "
        f"<b>{cliente.documento or 'não informado'}</b>, residente e domiciliado(a) em <b>{endereco}</b>, "
        f"doravante denominado(a) <b>COMPRADOR(A)</b>, firmam o presente contrato de venda e garantia.", corpo
    ))
    clausulas = [
        f"<b>Cláusula 1ª.</b> O presente contrato tem como objeto a venda do equipamento <b>{equipamento}</b>{pacote}, máquina/código <b>{eq.maquina or 'não informado'}</b> e número de série <b>{eq.numero_serie or 'não informado'}</b>.",
        f"<b>Cláusula 2ª.</b> O equipamento será entregue pela VENDEDORA em <b>{data_texto}</b>. Esta é a data de referência para o início da garantia.",
        f"<b>Cláusula 3ª.</b> O endereço de instalação informado pelo(a) COMPRADOR(A) é <b>{endereco}</b>.",
        f"<b>Cláusula 4ª.</b> O valor total da venda é <b>{valor}</b>.",
        f"<b>Cláusula 5ª.</b> A garantia do equipamento é de <b>{meses} meses a partir da data de entrega</b>. Para atendimento em garantia, o equipamento deverá ser levado à loja, salvo acordo diferente registrado por escrito.",
        "<b>Cláusula 6ª.</b> A garantia não cobre cabos, acessórios consumíveis, mau uso, quedas, líquidos, violação, intervenção de terceiros ou danos causados por falha e surto elétrico.",
        "<b>Cláusula 7ª.</b> Máquinas Premium, portáteis ou fliperamas devem utilizar estabilizador TS Shara 9101. Máquinas JBL e fliperamas de maior potência devem utilizar estabilizador TS Shara 9116, conforme orientação técnica da VENDEDORA.",
        "<b>Cláusula 8ª.</b> Recomenda-se a utilização de cabos de microfone Santo Ângelo XLR x P10 ou equivalentes de qualidade técnica compatível.",
        "<b>Cláusula 9ª.</b> Este contrato obriga as partes, seus herdeiros e sucessores.",
    ]
    for texto_clausula in clausulas:
        story.append(Paragraph(texto_clausula, corpo))
    story += [
        Spacer(1, 9),
        Paragraph("Por estarem justos e contratados, firmam o presente instrumento em duas vias de igual teor.", corpo),
        Spacer(1, 12),
        Paragraph(f"Rio de Janeiro, {data_texto}.", corpo),
        Spacer(1, 26),
        KeepTogether(Table([
            ["________________________________________", "________________________________________"],
            ["KARAOKE & GAMES RJ", cliente.nome],
            ["VENDEDORA", "COMPRADOR(A)"],
        ], colWidths=[85 * mm, 85 * mm], style=TableStyle([("ALIGN", (0,0), (-1,-1), "CENTER"), ("FONTNAME", (0,0), (-1,-1), nome_fonte), ("FONTSIZE", (0,0), (-1,-1), 8.5), ("TOPPADDING", (0,0), (-1,-1), 2), ("BOTTOMPADDING", (0,0), (-1,-1), 2)])))
    ]
    doc.build(story)
    nome = _nome_arquivo(cliente.nome)
    return Response(buffer.getvalue(), media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="contrato_garantia_{nome}_{eq.id}.pdf"'})


@app.get("/organiza/equipamentos/{equipamento_id}/nota.xml")
def dados_nota_xml(equipamento_id: int, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    eq = db.query(Equipamento).options(selectinload(Equipamento.cliente)).filter(Equipamento.id == equipamento_id).first()
    if not eq:
        raise HTTPException(404)
    c = eq.cliente
    raiz = ET.Element("dados_para_emissao")
    ET.SubElement(raiz, "aviso").text = "Arquivo de apoio para importacao. Nao e uma NF-e autorizada pela SEFAZ."
    dest = ET.SubElement(raiz, "destinatario")
    for chave, valor in {"nome":c.nome,"cpf_cnpj":c.documento,"inscricao_estadual":c.inscricao_estadual,"email":c.email,"telefone":c.telefone,"cep":c.cep,"logradouro":c.endereco,"numero":c.endereco_numero,"complemento":c.complemento,"bairro":c.bairro,"municipio":c.municipio or c.cidade,"uf":c.estado}.items():
        ET.SubElement(dest,chave).text = valor or ""
    item = ET.SubElement(raiz, "item")
    for chave, valor in {"descricao":f"{eq.tipo or ''} {eq.modelo or ''}".strip(),"codigo":eq.maquina,"numero_serie":eq.numero_serie,"valor_total":str(moeda_num(eq.valor or eq.preco_venda or '0')),"data_compra":eq.data_compra.isoformat() if eq.data_compra else ""}.items():
        ET.SubElement(item,chave).text = valor or ""
    conteudo = ET.tostring(raiz, encoding="utf-8", xml_declaration=True)
    return Response(conteudo, media_type="application/xml", headers={"Content-Disposition": f'attachment; filename="dados_nota_{eq.id}.xml"'})


@app.get("/organiza/equipamentos/{equipamento_id}/nota.csv")
def dados_nota_csv(equipamento_id: int, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    eq = db.query(Equipamento).options(selectinload(Equipamento.cliente)).filter(Equipamento.id == equipamento_id).first()
    if not eq:
        raise HTTPException(404)
    c = eq.cliente
    out = io.StringIO()
    campos = ["nome","cpf_cnpj","inscricao_estadual","email","telefone","cep","logradouro","numero","complemento","bairro","municipio","uf","descricao","codigo","numero_serie","valor_total","data_compra"]
    w = csv.DictWriter(out, fieldnames=campos, delimiter=';')
    w.writeheader()
    w.writerow({"nome":c.nome,"cpf_cnpj":c.documento or "","inscricao_estadual":c.inscricao_estadual or "","email":c.email or "","telefone":c.telefone,"cep":c.cep or "","logradouro":c.endereco or "","numero":c.endereco_numero or "","complemento":c.complemento or "","bairro":c.bairro or "","municipio":c.municipio or c.cidade or "","uf":c.estado or "","descricao":f"{eq.tipo or ''} {eq.modelo or ''}".strip(),"codigo":eq.maquina or "","numero_serie":eq.numero_serie or "","valor_total":str(moeda_num(eq.valor or eq.preco_venda or '0')).replace('.',','),"data_compra":eq.data_compra.strftime('%d/%m/%Y') if eq.data_compra else ""})
    conteudo = '\ufeff' + out.getvalue()
    return Response(conteudo.encode('utf-8'), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="dados_nota_{eq.id}.csv"'})


@app.get("/organiza/usuarios", response_class=HTMLResponse)
def usuarios(request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    exigir_admin(usuario)
    return templates.TemplateResponse("organiza/usuarios.html", {"request": request, "usuario": usuario, "usuarios": db.query(Usuario).order_by(Usuario.nome).all()})


def moeda_num(valor: str) -> float:
    texto = (valor or "0").replace("R$", "").strip().replace(".", "").replace(",", ".")
    try:
        return round(float(texto), 2)
    except ValueError:
        return 0.0


def totais_orcamento(orcamento: Orcamento):
    manutencao = max(float(orcamento.valor_manutencao or 0), 0)
    itens_obrigatorios = sum(i.preco_venda * i.quantidade for i in orcamento.itens if not i.opcional)
    obrigatorio = manutencao + itens_obrigatorios

    itens_opcionais = [i for i in orcamento.itens if i.opcional]
    opcionais = sum(i.preco_venda * i.quantidade for i in itens_opcionais)
    opcionais_aprovados = [i for i in itens_opcionais if i.aprovado]
    todos_opcionais_aprovados = bool(itens_opcionais) and len(opcionais_aprovados) == len(itens_opcionais)

    subtotal_aprovado = manutencao + sum(
        i.preco_venda * i.quantidade
        for i in orcamento.itens
        if (not i.opcional) or i.aprovado
    )

    desconto_informado = max(float(orcamento.desconto or 0), 0)
    desconto_condicional = bool(orcamento.desconto_somente_com_opcionais)

    # Desconto no valor efetivamente aprovado:
    # - normal: aplica sobre qualquer combinação aprovada;
    # - condicional: aplica apenas quando todos os opcionais forem aprovados.
    pode_aplicar_desconto = (not desconto_condicional) or todos_opcionais_aprovados
    desconto_aplicado = min(desconto_informado, subtotal_aprovado) if pode_aplicar_desconto else 0
    aprovado = max(subtotal_aprovado - desconto_aplicado, 0)

    geral_bruto = obrigatorio + opcionais
    # O total com todos os opcionais sempre atende à condição.
    geral = max(geral_bruto - min(desconto_informado, geral_bruto), 0)

    # No total obrigatório, o desconto condicional não é aplicado.
    desconto_no_obrigatorio = 0 if desconto_condicional else min(desconto_informado, obrigatorio)
    obrigatorio_final = max(obrigatorio - desconto_no_obrigatorio, 0)

    recebido = sum(p.valor for p in orcamento.pagamentos)
    return {
        "manutencao": manutencao,
        "itens_obrigatorios": itens_obrigatorios,
        "obrigatorio": obrigatorio,
        "obrigatorio_final": obrigatorio_final,
        "opcionais": opcionais,
        "geral_bruto": geral_bruto,
        "geral": geral,
        "subtotal_aprovado": subtotal_aprovado,
        "desconto": desconto_aplicado,
        "desconto_informado": desconto_informado,
        "desconto_condicional": desconto_condicional,
        "desconto_disponivel": pode_aplicar_desconto,
        "todos_opcionais_aprovados": todos_opcionais_aprovados,
        "aprovado": aprovado,
        "recebido": recebido,
        "falta": max(aprovado - recebido, 0),
    }


def carregar_manutencao(db: Session, manutencao_id: int):
    return db.query(Manutencao).options(
        selectinload(Manutencao.cliente),
        selectinload(Manutencao.equipamento),
        selectinload(Manutencao.orcamentos).selectinload(Orcamento.itens),
        selectinload(Manutencao.orcamentos).selectinload(Orcamento.pagamentos),
    ).filter(Manutencao.id == manutencao_id).first()


@app.get("/organiza/itens", response_class=HTMLResponse)
def itens_lista(request: Request, busca: str = "", usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    q = db.query(Item)
    termo = busca.strip()
    if termo:
        filtro = f"%{termo}%"
        q = q.filter(or_(Item.nome.ilike(filtro), Item.codigo.ilike(filtro), Item.categoria.ilike(filtro)))
    itens = q.order_by(Item.ativo.desc(), Item.categoria, Item.nome).all()
    categorias = [r[0] for r in db.query(Item.categoria).filter(Item.categoria.isnot(None)).distinct().order_by(Item.categoria).all() if r[0]]
    return templates.TemplateResponse("organiza/itens.html", {"request": request, "usuario": usuario, "itens": itens, "categorias": categorias, "busca": busca})


@app.post("/organiza/itens/novo")
async def item_novo(request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    form = dict(await request.form())
    nome = (form.get("nome") or "").strip()
    destino = (form.get("next") or "/organiza/itens").strip()
    if not destino.startswith("/"):
        destino = "/organiza/itens"
    if nome:
        existente = db.query(Item).filter(func.lower(Item.nome) == nome.lower()).first()
        if not existente:
            db.add(Item(
                nome=nome,
                codigo=(form.get("codigo") or "").strip() or None,
                categoria=(form.get("categoria") or "Geral").strip() or "Geral",
                preco_custo=moeda_num(form.get("preco_custo")),
                preco_venda=moeda_num(form.get("preco_venda")),
                ativo=1,
            ))
            db.commit()
    return RedirectResponse(destino, status_code=303)


@app.post("/organiza/itens/{item_id}/editar")
async def item_editar(item_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    item = db.query(Item).filter(Item.id == item_id).first()
    if not item:
        raise HTTPException(404)
    form = dict(await request.form())
    nome = (form.get("nome") or "").strip()
    repetido = db.query(Item).filter(func.lower(Item.nome) == nome.lower(), Item.id != item_id).first() if nome else None
    if nome and not repetido:
        item.nome = nome
        item.codigo = (form.get("codigo") or "").strip() or None
        item.categoria = (form.get("categoria") or "Geral").strip() or "Geral"
        item.preco_custo = moeda_num(form.get("preco_custo"))
        item.preco_venda = moeda_num(form.get("preco_venda"))
        db.commit()
    return RedirectResponse("/organiza/itens", status_code=303)


@app.post("/organiza/itens/{item_id}/status")
def item_status(item_id: int, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    item = db.query(Item).filter(Item.id == item_id).first()
    if not item:
        raise HTTPException(404)
    item.ativo = 0 if item.ativo else 1
    db.commit()
    return RedirectResponse("/organiza/itens", status_code=303)


@app.get("/organiza/manutencoes", response_class=HTMLResponse)
def manutencoes_lista(request: Request, status: str = "", usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    query = db.query(Manutencao).options(selectinload(Manutencao.cliente), selectinload(Manutencao.equipamento))
    filtros = {
        "entrada": ["Aguardando equipamento"],
        "orcamento": ["Recebida", "Orçamento em elaboração"],
        "aceite": ["Aguardando aprovação"],
        "pagamento": ["Aprovado"],
        "producao": ["Em manutenção"],
        "agenda": ["Pronto para retirada"],
        "retirada": ["Retirada agendada"],
        "encerradas": ["Encerrada"],
    }
    if status in filtros:
        query = query.filter(Manutencao.status.in_(filtros[status]))
    lista = query.order_by(Manutencao.criado_em.desc()).all()
    return templates.TemplateResponse("organiza/manutencoes.html", {"request": request, "usuario": usuario, "manutencoes": lista, "filtro_status": status})


@app.get("/organiza/manutencoes/nova", response_class=HTMLResponse)
def manutencao_nova(request: Request, cliente_id: int = 0, equipamento_id: int = 0, erro: str = "", usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    clientes = db.query(Cliente).options(selectinload(Cliente.equipamentos)).order_by(Cliente.nome).all()
    equipamentos_ativos = {
        cliente.id: ordenar_equipamentos([eq for eq in cliente.equipamentos if (eq.status or "Ativo") == "Ativo"])
        for cliente in clientes
    }
    return templates.TemplateResponse("organiza/manutencao_form.html", {
        "request": request, "usuario": usuario, "clientes": clientes,
        "equipamentos_ativos": equipamentos_ativos,
        "cliente_id": cliente_id, "equipamento_id": equipamento_id, "erro": erro
    })


@app.post("/organiza/manutencoes/nova")
async def manutencao_criar(request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    form = dict(await request.form())
    cliente_id = int(form.get("cliente_id") or 0); equipamento_id = int(form.get("equipamento_id") or 0)
    eq = db.query(Equipamento).filter(
        Equipamento.id == equipamento_id,
        Equipamento.cliente_id == cliente_id,
        Equipamento.status == "Ativo",
    ).first()
    if not eq or not (form.get("defeito") or "").strip():
        return RedirectResponse(f"/organiza/manutencoes/nova?cliente_id={cliente_id}&equipamento_id={equipamento_id}", status_code=303)
    tipo_atendimento = (form.get("tipo_atendimento") or "loja").strip().lower()
    if tipo_atendimento not in ("loja", "online"):
        tipo_atendimento = "loja"
    agendamento = datetime_form(form.get("entrega_prevista_em") or "")
    if not horario_atendimento_valido(tipo_atendimento, agendamento):
        return RedirectResponse(f"/organiza/manutencoes/nova?cliente_id={cliente_id}&equipamento_id={equipamento_id}&erro=horario", status_code=303)
    if horario_atendimento_ocupado(db, agendamento):
        return RedirectResponse(f"/organiza/manutencoes/nova?cliente_id={cliente_id}&equipamento_id={equipamento_id}&erro=ocupado", status_code=303)
    status_inicial = "Aguardando equipamento"
    m = Manutencao(cliente_id=cliente_id, equipamento_id=equipamento_id, defeito=form.get("defeito").strip(), observacao=(form.get("observacao") or "").strip() or None, entrega_prevista_em=agendamento, tipo_atendimento=tipo_atendimento, status=status_inicial)
    db.add(m); db.commit(); db.refresh(m)
    o = Orcamento(manutencao_id=m.id, versao=1, token=secrets.token_urlsafe(24), status="Rascunho")
    db.add(o); db.commit()
    return RedirectResponse(f"/organiza/manutencoes/{m.id}", status_code=303)


@app.get("/organiza/manutencoes/{manutencao_id}", response_class=HTMLResponse)
def manutencao_detalhe(manutencao_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    m = carregar_manutencao(db, manutencao_id)
    if not m: raise HTTPException(404)
    itens = db.query(Item).filter(Item.ativo == 1).order_by(Item.nome).all()
    equipamentos_cliente = ordenar_equipamentos(
        db.query(Equipamento).filter(
            Equipamento.cliente_id == m.cliente_id,
            or_(Equipamento.status == "Ativo", Equipamento.id == m.equipamento_id),
        ).all()
    )
    orcamento = sorted(m.orcamentos, key=lambda x: x.versao)[-1] if m.orcamentos else None
    totais = totais_orcamento(orcamento) if orcamento else {}
    prontas_cliente = (
        db.query(Manutencao)
        .options(selectinload(Manutencao.equipamento))
        .filter(
            Manutencao.cliente_id == m.cliente_id,
            Manutencao.pronto_em.isnot(None),
            Manutencao.entregue_em.is_(None),
            Manutencao.status.in_(("Pronto para retirada", "Retirada agendada")),
        )
        .order_by(Manutencao.pronto_em.asc(), Manutencao.id.asc())
        .all()
    )
    linhas_prontas = []
    for pronta in prontas_cliente:
        equipamento = pronta.equipamento
        identificacao = rotulo_maquina(equipamento)
        descricao = f"{equipamento.tipo} {equipamento.modelo or ''}".strip()
        linhas_prontas.append(f"• {identificacao} · {descricao}\n  Código técnico: {codigo_tecnico(equipamento)}")
    mensagem_retirada = (
        f"Olá, {m.cliente.nome}. Os equipamentos abaixo estão prontos para retirada:\n"
        + "\n".join(linhas_prontas)
        + f"\n\n📄 Garantia do serviço (30 dias): {PUBLIC_BASE_URL}/garantia-servico/{orcamento.token}.pdf"
        + f"\n\n📅 Escolha a data e o horário da retirada: {PUBLIC_BASE_URL}/retirada/{orcamento.token}"
        + "\n\nRetiradas de segunda a sexta-feira, somente das 14:00 às 17:00."
        + "\n\nKaraokê RJ"
    ) if orcamento and prontas_cliente else ""
    return templates.TemplateResponse("organiza/manutencao_detalhe.html", {"request": request, "usuario": usuario, "m": m, "orcamento": orcamento, "itens_catalogo": itens, "equipamentos_cliente": equipamentos_cliente, "totais": totais, "etapa_atual": etapa_manutencao(m), "manutencoes_prontas_cliente": prontas_cliente, "mensagem_retirada": mensagem_retirada})


@app.post("/organiza/manutencoes/{manutencao_id}/orcamento/item")
async def orcamento_adicionar_item(manutencao_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    m = carregar_manutencao(db, manutencao_id); form = dict(await request.form())
    if not m: raise HTTPException(404)
    o = sorted(m.orcamentos, key=lambda x: x.versao)[-1]
    item = db.query(Item).filter(Item.id == int(form.get("item_id") or 0)).first()
    descricao = item.nome if item else (form.get("descricao") or "").strip()
    descricao_normalizada = unicodedata.normalize("NFKD", descricao).encode("ascii", "ignore").decode("ascii").strip().lower()
    if descricao_normalizada == "manutencao":
        return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}?erro=Use o campo Valor obrigatório da manutenção", status_code=303)
    if descricao:
        db.add(OrcamentoItem(orcamento_id=o.id, item_id=item.id if item else None, descricao=descricao, quantidade=max(int(form.get("quantidade") or 1),1), preco_custo=item.preco_custo if item else moeda_num(form.get("preco_custo")), preco_venda=moeda_num(form.get("preco_venda")) or (item.preco_venda if item else 0), opcional=1 if form.get("opcional") else 0, aprovado=0 if form.get("opcional") else 1))
        db.commit()
    return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}", status_code=303)


@app.post("/organiza/manutencoes/{manutencao_id}/orcamento/item/{orcamento_item_id}/excluir")
def orcamento_excluir_item(manutencao_id: int, orcamento_item_id: int, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    m = carregar_manutencao(db, manutencao_id)
    if not m:
        raise HTTPException(404)
    orcamento_ids = [o.id for o in m.orcamentos]
    item = db.query(OrcamentoItem).filter(OrcamentoItem.id == orcamento_item_id, OrcamentoItem.orcamento_id.in_(orcamento_ids)).first()
    if item:
        db.delete(item)
        db.commit()
    return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}", status_code=303)


@app.post("/organiza/manutencoes/{manutencao_id}/orcamento/manutencao")
async def orcamento_salvar_manutencao(manutencao_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    m = carregar_manutencao(db, manutencao_id)
    if not m or not m.orcamentos:
        raise HTTPException(404)
    form = dict(await request.form())
    o = sorted(m.orcamentos, key=lambda x: x.versao)[-1]
    o.valor_manutencao = max(moeda_num(form.get("valor_manutencao")), 0)
    db.commit()
    return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}", status_code=303)


@app.post("/organiza/manutencoes/{manutencao_id}/orcamento/desconto")
async def orcamento_salvar_desconto(manutencao_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    m = carregar_manutencao(db, manutencao_id)
    if not m or not m.orcamentos:
        raise HTTPException(404)
    form = dict(await request.form())
    o = sorted(m.orcamentos, key=lambda x: x.versao)[-1]
    subtotal = max(float(o.valor_manutencao or 0), 0) + sum(i.preco_venda * i.quantidade for i in o.itens)
    o.desconto = min(max(moeda_num(form.get("desconto")), 0), subtotal)
    o.desconto_somente_com_opcionais = 1 if form.get("desconto_somente_com_opcionais") else 0
    db.commit()
    return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}", status_code=303)


@app.post("/organiza/manutencoes/{manutencao_id}/orcamento/condicoes")
async def orcamento_salvar_condicoes(manutencao_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    m = carregar_manutencao(db, manutencao_id)
    if not m or not m.orcamentos:
        raise HTTPException(404)
    form = dict(await request.form())
    o = sorted(m.orcamentos, key=lambda x: x.versao)[-1]
    forma = (form.get("forma_pagamento_orcamento") or "").strip()
    if forma not in ("À vista", "50% de sinal + 50% na entrega"):
        forma = ""
    try:
        prazo = int(form.get("prazo_dias_uteis") or 0)
    except (TypeError, ValueError):
        prazo = 0
    o.forma_pagamento_orcamento = forma or None
    o.prazo_dias_uteis = prazo if prazo > 0 else None
    db.commit()
    return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}", status_code=303)


@app.post("/organiza/manutencoes/{manutencao_id}/orcamento/enviar")
def orcamento_enviar(manutencao_id: int, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    m = carregar_manutencao(db, manutencao_id); o = sorted(m.orcamentos, key=lambda x: x.versao)[-1]
    if float(o.valor_manutencao or 0) <= 0: return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}?erro=Informe o valor obrigatório da manutenção", status_code=303)
    if not o.forma_pagamento_orcamento: return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}?erro=Informe a forma de pagamento do orçamento", status_code=303)
    if not o.prazo_dias_uteis or o.prazo_dias_uteis <= 0: return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}?erro=Informe o prazo em dias úteis após o pagamento", status_code=303)
    o.status = "Enviado"; m.status = "Aguardando aprovação"; db.commit()
    return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}", status_code=303)


@app.post("/organiza/manutencoes/{manutencao_id}/aprovar-manual")
def aprovar_manual(manutencao_id: int, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    m = carregar_manutencao(db, manutencao_id); o = sorted(m.orcamentos, key=lambda x: x.versao)[-1]
    for i in o.itens: i.aprovado = 1
    o.status = "Aprovado manualmente"; o.aprovado_em = datetime.now(); m.status = "Aprovado"; db.commit()
    return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}", status_code=303)


@app.post("/organiza/manutencoes/{manutencao_id}/pagamento")
async def pagamento_registrar(manutencao_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    m = carregar_manutencao(db, manutencao_id); form = dict(await request.form()); o = sorted(m.orcamentos, key=lambda x: x.versao)[-1]
    valor = moeda_num(form.get("valor"))
    if valor > 0:
        db.add(Pagamento(orcamento_id=o.id, data=data_form(form.get("data") or "") or date.today(), valor=valor, forma=(form.get("forma") or "PIX").strip(), observacao=(form.get("observacao") or "").strip() or None))
        m.status = "Confirmação pendente"; db.commit()
    return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}", status_code=303)


@app.post("/organiza/manutencoes/{manutencao_id}/prazo")
async def prazo_salvar(manutencao_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    m = db.query(Manutencao).filter(Manutencao.id == manutencao_id).first(); form = dict(await request.form())
    m.prazo = (form.get("prazo") or "").strip() or None; db.commit()
    return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}", status_code=303)



def _orcamento_atual(m):
    return sorted(m.orcamentos, key=lambda x: x.versao)[-1] if m.orcamentos else None


@app.get("/organiza/manutencoes/{manutencao_id}/confirmar-prazo-whatsapp")
def confirmar_prazo_whatsapp(manutencao_id: int, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    m = carregar_manutencao(db, manutencao_id)
    if not m:
        raise HTTPException(404)
    o = _orcamento_atual(m)
    recebido = sum(p.valor for p in o.pagamentos) if o else 0
    if not o or recebido <= 0 or not m.prazo:
        return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}?erro_fluxo=pagamento_prazo", status_code=303)
    m.confirmacao_prazo_em = datetime.now()
    m.status = "Em manutenção"
    db.commit()
    mensagem = (
        f"Olá, {m.cliente.nome}!\n\n"
        "✅ Pagamento e prazo confirmados.\n\n"
        f"Equipamento: {descricao_equipamento(m.equipamento)}\n"
        f"Código técnico: {codigo_tecnico(m.equipamento)}\n"
        f"Prazo previsto: {m.prazo}\n\n"
        "Agora iniciaremos a execução do serviço.\n\nKaraokê RJ"
    )
    return RedirectResponse(f"https://wa.me/55{m.cliente.telefone}?text={quote(mensagem)}", status_code=303)


@app.post("/organiza/manutencoes/{manutencao_id}/pausar-servico")
async def pausar_servico(manutencao_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    m = carregar_manutencao(db, manutencao_id)
    if not m or etapa_manutencao(m) != 5:
        raise HTTPException(400, "Etapa indisponível.")
    form = dict(await request.form())
    descricao = (form.get("compra_descricao") or "").strip()
    previsao = (form.get("compra_previsao") or "").strip()
    if not descricao:
        return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}?erro_fluxo=compra", status_code=303)
    m.compra_descricao = descricao
    m.compra_previsao = previsao or None
    m.servico_pausado_em = datetime.now()
    m.compra_comunicada_em = None
    m.status = "Aguardando peça"
    db.commit()
    return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}#etapa-5", status_code=303)


@app.post("/organiza/manutencoes/{manutencao_id}/retomar-servico")
def retomar_servico(manutencao_id: int, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    m = carregar_manutencao(db, manutencao_id)
    if not m:
        raise HTTPException(404)
    m.servico_pausado_em = None
    m.status = "Em manutenção"
    db.commit()
    return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}#etapa-5", status_code=303)


@app.get("/organiza/manutencoes/{manutencao_id}/comunicar-pausa-whatsapp")
def comunicar_pausa_whatsapp(manutencao_id: int, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    m = carregar_manutencao(db, manutencao_id)
    if not m or not m.servico_pausado_em or not m.compra_descricao:
        return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}?erro_fluxo=compra", status_code=303)
    m.compra_comunicada_em = datetime.now()
    db.commit()
    mensagem = (
        f"Olá, {m.cliente.nome}!\n\n"
        "⏸️ Durante a execução do serviço identificamos a necessidade de comprar:\n"
        f"{m.compra_descricao}\n"
        + (f"Previsão: {m.compra_previsao}\n" if m.compra_previsao else "")
        + "\nO serviço ficará pausado até a chegada do item. Manteremos você informado.\n\nKaraokê RJ"
    )
    return RedirectResponse(f"https://wa.me/55{m.cliente.telefone}?text={quote(mensagem)}", status_code=303)


@app.get("/organiza/manutencoes/{manutencao_id}/concluir-whatsapp")
def concluir_servico_whatsapp(manutencao_id: int, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    m = carregar_manutencao(db, manutencao_id)
    if not m:
        raise HTTPException(404)
    if etapa_manutencao(m) != 5 or m.servico_pausado_em or not (m.diagnostico or "").strip():
        return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}?erro_fluxo=conclusao", status_code=303)
    o = _orcamento_atual(m)
    if not o:
        raise HTTPException(400, "Orçamento não encontrado.")
    agora = datetime.now()
    m.pronto_em = agora
    m.conclusao_comunicada_em = agora
    m.status = "Pronto para retirada"
    db.commit()
    mensagem = (
        f"Olá, {m.cliente.nome}!\n\n"
        "✅ Seu serviço foi concluído.\n\n"
        f"Equipamento: {descricao_equipamento(m.equipamento)}\n"
        f"Código técnico: {codigo_tecnico(m.equipamento)}\n\n"
        f"📄 Garantia de 30 dias: {PUBLIC_BASE_URL}/garantia-servico/{o.token}.pdf\n"
        f"📅 Agende a retirada: {PUBLIC_BASE_URL}/retirada/{o.token}\n\n"
        "Karaokê RJ"
    )
    return RedirectResponse(f"https://wa.me/55{m.cliente.telefone}?text={quote(mensagem)}", status_code=303)


@app.get("/garantia-servico/{token}.pdf")
def garantia_servico_pdf(token: str, db: Session = Depends(get_db)):
    """Certificado público de 30 dias, acessível pelo link enviado ao cliente."""
    orcamento = db.query(Orcamento).filter(Orcamento.token == token).first()
    if not orcamento:
        raise HTTPException(404)
    m = (
        db.query(Manutencao)
        .options(selectinload(Manutencao.cliente), selectinload(Manutencao.equipamento), selectinload(Manutencao.orcamentos))
        .filter(Manutencao.id == orcamento.manutencao_id)
        .first()
    )
    if not m or not m.pronto_em:
        raise HTTPException(404)
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_CENTER
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image, Table, TableStyle
    from reportlab.lib import colors
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=18*mm, leftMargin=18*mm, topMargin=15*mm, bottomMargin=15*mm)
    estilos = getSampleStyleSheet()
    titulo = ParagraphStyle("TituloGarantia", parent=estilos["Title"], alignment=TA_CENTER, fontSize=18, leading=22, spaceAfter=8)
    centro = ParagraphStyle("CentroGarantia", parent=estilos["BodyText"], alignment=TA_CENTER, fontSize=10, leading=14)
    corpo = ParagraphStyle("CorpoGarantia", parent=estilos["BodyText"], fontSize=10, leading=15, spaceAfter=8)
    elementos = []
    logo_path = os.path.join(os.path.dirname(__file__), "static", "img", "logo-karaoke-rj.png")
    if os.path.exists(logo_path):
        elementos += [Image(logo_path, width=32*mm, height=32*mm), Spacer(1, 4*mm)]
    elementos += [
        Paragraph("CERTIFICADO DE GARANTIA DO SERVIÇO", titulo),
        Paragraph("KARAOKÊ RJ", centro),
        Spacer(1, 7*mm),
    ]
    eq = m.equipamento
    dados = [
        ["Ordem de serviço", f"#{m.id}"],
        ["Cliente", m.cliente.nome],
        ["Equipamento", descricao_equipamento(eq)],
        ["Código técnico", codigo_tecnico(eq)],
        ["Serviço concluído em", m.pronto_em.strftime("%d/%m/%Y")],
        ["Garantia válida até", (m.pronto_em.date() + timedelta(days=30)).strftime("%d/%m/%Y")],
    ]
    tabela = Table(dados, colWidths=[48*mm, 110*mm])
    tabela.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (0,-1), colors.HexColor("#F2F4F7")),
        ("TEXTCOLOR", (0,0), (0,-1), colors.HexColor("#344054")),
        ("FONTNAME", (0,0), (0,-1), "Helvetica-Bold"),
        ("FONTNAME", (1,0), (1,-1), "Helvetica"),
        ("GRID", (0,0), (-1,-1), .4, colors.HexColor("#D0D5DD")),
        ("VALIGN", (0,0), (-1,-1), "TOP"),
        ("PADDING", (0,0), (-1,-1), 7),
    ]))
    elementos += [tabela, Spacer(1, 8*mm)]
    elementos += [
        Paragraph("<b>Prazo e cobertura</b>", corpo),
        Paragraph("Garantia de 30 dias sobre os serviços executados nesta ordem de serviço, contados a partir da data de conclusão. A garantia cobre exclusivamente o serviço realizado e não inclui mau uso, quedas, líquidos, ligação em tensão incorreta, intervenção de terceiros ou defeitos diferentes do reparo executado.", corpo),
        Spacer(1, 7*mm),
        Paragraph("Karaokê RJ · Rua João Romariz, 313 - Ramos - Rio de Janeiro/RJ<br/>WhatsApp: (21) 99507-9690 / (21) 99650-4516 · www.karaokerj.com.br", centro),
    ]
    doc.build(elementos)
    return Response(buffer.getvalue(), media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="garantia-os-{m.id}.pdf"'})

@app.post("/organiza/manutencoes/{manutencao_id}/pronto")
def manutencao_pronto(manutencao_id: int, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    m = db.query(Manutencao).filter(Manutencao.id == manutencao_id).first(); m.status = "Pronto para retirada"; m.pronto_em = datetime.now(); db.commit()
    return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}", status_code=303)


@app.post("/organiza/manutencoes/{manutencao_id}/retirada")
async def retirada_agendar(manutencao_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    m = db.query(Manutencao).filter(Manutencao.id == manutencao_id).first(); form = dict(await request.form())
    m.retirada_em = datetime_form(form.get("retirada_em") or "")
    if m.retirada_em and m.retirada_em.weekday() < 5 and time(14,0) <= m.retirada_em.time() <= time(17,0): m.status = "Retirada agendada"
    else: m.retirada_em = None
    db.commit(); return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}", status_code=303)



@app.post("/organiza/manutencoes/{manutencao_id}/editar")
async def manutencao_editar(manutencao_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    m = db.query(Manutencao).filter(Manutencao.id == manutencao_id).first()
    if not m: raise HTTPException(404)
    form = dict(await request.form())
    # O equipamento é corrigido por uma ação exclusiva e confirmada.
    # Este formulário altera apenas os demais dados da Etapa 1.
    m.defeito = (form.get("defeito") or "").strip()
    m.observacao = (form.get("observacao") or "").strip() or None
    m.diagnostico = (form.get("diagnostico") or "").strip() or None
    tipo_atendimento = (form.get("tipo_atendimento") or m.tipo_atendimento or "loja").strip().lower()
    agendamento = datetime_form(form.get("entrega_prevista_em") or "")
    if tipo_atendimento not in ("loja", "online") or not horario_atendimento_valido(tipo_atendimento, agendamento) or horario_atendimento_ocupado(db, agendamento, m.id):
        return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}?erro_agendamento=1", status_code=303)
    m.tipo_atendimento = tipo_atendimento
    m.entrega_prevista_em = agendamento
    db.commit(); return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}", status_code=303)

@app.post("/organiza/manutencoes/{manutencao_id}/corrigir-equipamento")
async def manutencao_corrigir_equipamento(
    manutencao_id: int,
    request: Request,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    """Corrige o equipamento da OS e mantém todo o histórico ligado ao equipamento correto."""
    m = (
        db.query(Manutencao)
        .options(selectinload(Manutencao.equipamento), selectinload(Manutencao.cliente))
        .filter(Manutencao.id == manutencao_id)
        .first()
    )
    if not m:
        raise HTTPException(404)

    form = dict(await request.form())
    try:
        equipamento_id = int(form.get("equipamento_id") or 0)
    except (TypeError, ValueError):
        equipamento_id = 0

    equipamento_novo = db.query(Equipamento).filter(
        Equipamento.id == equipamento_id,
        Equipamento.status == "Ativo",
    ).first()
    if not equipamento_novo:
        return RedirectResponse(
            f"/organiza/manutencoes/{manutencao_id}?erro_equipamento=1#etapa-1",
            status_code=303,
        )

    # A manutenção pertence ao equipamento. O cliente é sempre derivado do dono atual dele.
    equipamento_antigo = m.equipamento
    if equipamento_antigo and equipamento_antigo.id == equipamento_novo.id:
        return RedirectResponse(
            f"/organiza/manutencoes/{manutencao_id}?equipamento_inalterado=1#etapa-1",
            status_code=303,
        )

    identificacao_antiga = (
        f"{rotulo_maquina(equipamento_antigo)} / {codigo_tecnico(equipamento_antigo)}"
        if equipamento_antigo else "não informado"
    )
    identificacao_nova = f"{rotulo_maquina(equipamento_novo)} / {codigo_tecnico(equipamento_novo)}"
    registro = (
        f"[{datetime.now().strftime('%d/%m/%Y %H:%M')}] "
        f"Equipamento corrigido de {identificacao_antiga} para {identificacao_nova}."
    )

    m.equipamento_id = equipamento_novo.id
    m.cliente_id = equipamento_novo.cliente_id
    observacao_atual = (m.observacao or "").strip()
    m.observacao = f"{observacao_atual}\n{registro}".strip()
    db.commit()

    return RedirectResponse(
        f"/organiza/manutencoes/{manutencao_id}?equipamento_corrigido=1#etapa-1",
        status_code=303,
    )


@app.post("/organiza/manutencoes/{manutencao_id}/receber")
def manutencao_receber(manutencao_id: int, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    m = db.query(Manutencao).filter(Manutencao.id == manutencao_id).first()
    if not m: raise HTTPException(404)
    m.recebido_em = datetime.now(); m.status = "Orçamento em elaboração"; db.commit()
    return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}", status_code=303)

@app.post("/organiza/manutencoes/{manutencao_id}/orcamento/item/{orcamento_item_id}/editar")
async def orcamento_item_editar(manutencao_id: int, orcamento_item_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    form = dict(await request.form())
    item = db.query(OrcamentoItem).filter(OrcamentoItem.id == orcamento_item_id).first()
    if not item: raise HTTPException(404)
    item.descricao = (form.get("descricao") or item.descricao).strip()
    item.quantidade = max(int(form.get("quantidade") or 1), 1)
    item.preco_venda = moeda_num(form.get("preco_venda"))
    item.opcional = 1 if form.get("opcional") else 0
    if not item.opcional: item.aprovado = 1
    db.commit(); return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}", status_code=303)

@app.post("/organiza/manutencoes/{manutencao_id}/pagamento/{pagamento_id}/editar")
async def pagamento_editar(manutencao_id: int, pagamento_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    p = db.query(Pagamento).filter(Pagamento.id == pagamento_id).first()
    if not p: raise HTTPException(404)
    form = dict(await request.form()); p.data = data_form(form.get("data") or "") or p.data; p.valor = moeda_num(form.get("valor")); p.forma=(form.get("forma") or "PIX").strip(); p.observacao=(form.get("observacao") or "").strip() or None
    db.commit(); return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}", status_code=303)

@app.post("/organiza/manutencoes/{manutencao_id}/pagamento/{pagamento_id}/excluir")
def pagamento_excluir(manutencao_id: int, pagamento_id: int, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    p=db.query(Pagamento).filter(Pagamento.id==pagamento_id).first()
    if p: db.delete(p); db.commit()
    return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}", status_code=303)

@app.post("/organiza/manutencoes/{manutencao_id}/encerrar")
def manutencao_encerrar(manutencao_id: int, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    m=db.query(Manutencao).filter(Manutencao.id==manutencao_id).first()
    if not m or not m.retirada_em: raise HTTPException(400, "Agende a retirada antes de encerrar")
    m.entregue_em=datetime.now(); m.status="Encerrada"; db.commit()
    return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}", status_code=303)

@app.get("/organiza/agenda", response_class=HTMLResponse)
def agenda(request: Request, etapa: int = 0, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    manutencoes = (
        db.query(Manutencao)
        .options(
            selectinload(Manutencao.cliente),
            selectinload(Manutencao.equipamento),
            selectinload(Manutencao.orcamentos),
        )
        .filter(
            Manutencao.entregue_em.is_(None),
            ~Manutencao.status.in_(("Encerrada", "Cancelada")),
        )
        .all()
    )

    agenda_manutencoes = []
    for manutencao in manutencoes:
        numero = etapa_manutencao(manutencao)
        if numero == 7 or (etapa and numero != etapa):
            continue
        manutencao.agenda_etapa = numero
        manutencao.agenda_info = ETAPAS_MANUTENCAO[numero]
        manutencao.agenda_data = data_etapa_manutencao(manutencao)
        agenda_manutencoes.append(manutencao)

    agenda_manutencoes.sort(
        key=lambda item: (
            item.agenda_etapa,
            item.agenda_data or datetime.max,
            item.id,
        )
    )

    status_venda_pendentes = ("Solicitar gabinete", "Montagem", "Pronto para entrega")
    vendas = (
        db.query(Equipamento)
        .options(selectinload(Equipamento.cliente))
        .filter(
            Equipamento.previsao_entrega.isnot(None),
            Equipamento.status.in_(status_venda_pendentes),
        )
        .order_by(Equipamento.previsao_entrega.asc())
        .all()
    )

    return templates.TemplateResponse(
        "organiza/agenda.html",
        {
            "request": request,
            "usuario": usuario,
            "manutencoes": agenda_manutencoes,
            "vendas": vendas,
            "etapa_filtro": etapa,
            "etapas": ETAPAS_MANUTENCAO,
        },
    )

def manutencoes_prontas_cliente(db: Session, cliente_id: int):
    return (
        db.query(Manutencao)
        .options(selectinload(Manutencao.cliente), selectinload(Manutencao.equipamento))
        .filter(
            Manutencao.cliente_id == cliente_id,
            Manutencao.pronto_em.isnot(None),
            Manutencao.entregue_em.is_(None),
            Manutencao.status.in_(("Pronto para retirada", "Retirada agendada")),
        )
        .order_by(Manutencao.pronto_em.asc(), Manutencao.id.asc())
        .all()
    )


def contexto_retirada_publica(request: Request, o: Orcamento, prontas, erro: str = ""):
    return {
        "request": request,
        "orcamento": o,
        "m": o.manutencao,
        "manutencoes_prontas": prontas,
        "erro": erro,
        "hoje": date.today().isoformat(),
    }


@app.get("/retirada/{token}", response_class=HTMLResponse)
def retirada_publica(token: str, request: Request, db: Session = Depends(get_db)):
    o = (
        db.query(Orcamento)
        .options(
            selectinload(Orcamento.manutencao).selectinload(Manutencao.cliente),
            selectinload(Orcamento.manutencao).selectinload(Manutencao.equipamento),
        )
        .filter(Orcamento.token == token)
        .first()
    )
    if not o or not o.manutencao.pronto_em:
        raise HTTPException(404)
    prontas = manutencoes_prontas_cliente(db, o.manutencao.cliente_id)
    if not prontas:
        raise HTTPException(404)
    return templates.TemplateResponse(
        "organiza/retirada_publica.html",
        contexto_retirada_publica(request, o, prontas),
    )


@app.post("/retirada/{token}")
async def retirada_publica_salvar(token: str, request: Request, db: Session = Depends(get_db)):
    o = (
        db.query(Orcamento)
        .options(selectinload(Orcamento.manutencao))
        .filter(Orcamento.token == token)
        .first()
    )
    if not o or not o.manutencao.pronto_em:
        raise HTTPException(404)

    prontas = manutencoes_prontas_cliente(db, o.manutencao.cliente_id)
    if not prontas:
        raise HTTPException(404)

    form = dict(await request.form())
    data_retirada = data_form(form.get("data_retirada") or "")
    hora_texto = (form.get("hora_retirada") or "").strip()
    dt = None
    try:
        hora_retirada = datetime.strptime(hora_texto, "%H:%M").time()
        if data_retirada:
            dt = datetime.combine(data_retirada, hora_retirada)
    except ValueError:
        pass

    erro = "Escolha uma data e um horário válidos, de segunda a sexta, entre 14:00 e 17:00."
    agora = datetime.now()
    if (
        dt
        and dt >= agora
        and dt.weekday() < 5
        and time(14, 0) <= dt.time() <= time(17, 0)
    ):
        for manutencao in prontas:
            manutencao.retirada_em = dt
            manutencao.status = "Retirada agendada"
        db.commit()
        return RedirectResponse(f"/retirada/{token}?ok=1", status_code=303)

    return templates.TemplateResponse(
        "organiza/retirada_publica.html",
        contexto_retirada_publica(request, o, prontas, erro),
        status_code=400,
    )

@app.get("/orcamento/{token}", response_class=HTMLResponse)
def orcamento_publico(token: str, request: Request, db: Session = Depends(get_db)):
    o = db.query(Orcamento).options(selectinload(Orcamento.itens), selectinload(Orcamento.manutencao).selectinload(Manutencao.cliente), selectinload(Orcamento.manutencao).selectinload(Manutencao.equipamento)).filter(Orcamento.token == token).first()
    if not o: raise HTTPException(404)
    return templates.TemplateResponse("organiza/orcamento_publico.html", {"request": request, "orcamento": o, "m": o.manutencao, "totais": totais_orcamento(o)})


@app.post("/orcamento/{token}/responder")
async def orcamento_responder(token: str, request: Request, db: Session = Depends(get_db)):
    o = db.query(Orcamento).options(selectinload(Orcamento.itens), selectinload(Orcamento.manutencao)).filter(Orcamento.token == token).first(); form = dict(await request.form())
    if not o: raise HTTPException(404)
    acao = form.get("acao")
    if acao == "cancelar":
        o.status = "Cancelado"; o.manutencao.status = "Cancelado"
    else:
        selecionados = {int(v) for k,v in form.items() if k.startswith("opcional_") and str(v).isdigit()}
        for i in o.itens:
            i.aprovado = 1 if (not i.opcional or acao == "aprovar" or i.id in selecionados) else 0
        o.status = "Aprovado" if acao == "aprovar" else "Aprovado obrigatório"
        o.aprovado_em = datetime.now(); o.manutencao.status = "Aprovado"
    db.commit(); return RedirectResponse(f"/orcamento/{token}?ok=1", status_code=303)

# -----------------------------------------------------------------------------
# Portal público simplificado para solicitação de manutenção
# -----------------------------------------------------------------------------
HORARIOS_LOJA = ["14:00", "14:30", "15:00", "15:30", "16:00", "16:30", "17:00"]
HORARIOS_ONLINE = ["11:00", "11:30", "12:00", "12:30", "13:00", "13:30", "14:00", "14:30", "15:00", "15:30", "16:00", "16:30", "17:00"]
HORARIOS_ENTREGA_PUBLICA = HORARIOS_LOJA

def horarios_atendimento(tipo: str):
    return HORARIOS_ONLINE if tipo == "online" else HORARIOS_LOJA

def horario_atendimento_valido(tipo: str, momento):
    return bool(momento and momento.weekday() < 5 and momento.strftime("%H:%M") in horarios_atendimento(tipo))

def horario_atendimento_ocupado(db: Session, momento, ignorar_id: int = 0):
    q = db.query(Manutencao).filter(Manutencao.entrega_prevista_em == momento, Manutencao.status != "Encerrada")
    if ignorar_id:
        q = q.filter(Manutencao.id != ignorar_id)
    return q.first() is not None
STATUS_MANUTENCAO_ENCERRADOS = ["Encerrada", "Cancelada"]


def cliente_por_whatsapp(db: Session, telefone: str):
    numero = limpar_telefone(telefone)
    if not telefone_valido(numero):
        return None
    # Os telefones antigos podem estar formatados de maneiras diferentes.
    # A comparação normalizada preserva compatibilidade com os cadastros atuais.
    for cliente in db.query(Cliente).options(selectinload(Cliente.equipamentos)).all():
        if limpar_telefone(cliente.telefone) == numero:
            return cliente
    return None


def equipamento_com_manutencao_aberta(db: Session, equipamento_id: int):
    return db.query(Manutencao).filter(
        Manutencao.equipamento_id == equipamento_id,
        Manutencao.status.notin_(STATUS_MANUTENCAO_ENCERRADOS),
    ).order_by(Manutencao.criado_em.desc()).first()


def equipamentos_portal(db: Session, cliente: Cliente):
    """No chamado público aparecem somente equipamentos ativos."""
    resultado = []
    ativos = [e for e in cliente.equipamentos if (e.status or "Ativo") == "Ativo"]
    for equipamento in ordenar_equipamentos(ativos):
        aberta = equipamento_com_manutencao_aberta(db, equipamento.id)
        resultado.append({"equipamento": equipamento, "manutencao_aberta": aberta})
    return resultado


@app.get("/solicitar-manutencao", response_class=HTMLResponse)
def manutencao_publica_inicio(request: Request):
    return templates.TemplateResponse("organiza/manutencao_publica.html", {
        "request": request,
        "etapa": "telefone",
        "erro": "",
        "telefone": "",
        "horarios": HORARIOS_ENTREGA_PUBLICA,
    })


def renderizar_equipamentos_publicos(request: Request, db: Session, cliente: Cliente, telefone: str, erro: str = "", form_anterior=None, status_code: int = 200):
    return templates.TemplateResponse("organiza/manutencao_publica.html", {
        "request": request,
        "etapa": "equipamentos",
        "erro": erro,
        "telefone": telefone,
        "cliente": cliente,
        "equipamentos": equipamentos_portal(db, cliente),
        "horarios": HORARIOS_LOJA,
        "horarios_loja": HORARIOS_LOJA,
        "horarios_online": HORARIOS_ONLINE,
        "data_minima": date.today().isoformat(),
        "form_anterior": form_anterior,
    }, status_code=status_code)


@app.post("/solicitar-manutencao/pesquisar", response_class=HTMLResponse)
async def manutencao_publica_pesquisar(request: Request, db: Session = Depends(get_db)):
    try:
        form = dict(await request.form())
    except ClientDisconnect:
        return RedirectResponse("/solicitar-manutencao", status_code=303)
    telefone = limpar_telefone(form.get("telefone") or "")
    cliente = cliente_por_whatsapp(db, telefone)
    if not cliente:
        return templates.TemplateResponse("organiza/manutencao_publica.html", {
            "request": request,
            "etapa": "telefone",
            "erro": "WhatsApp não encontrado. Informe o mesmo número utilizado no cadastro, com DDD.",
            "telefone": telefone,
            "horarios": HORARIOS_ENTREGA_PUBLICA,
        }, status_code=400)

    return templates.TemplateResponse("organiza/manutencao_publica.html", {
        "request": request,
        "etapa": "revisar_dados",
        "erro": "",
        "telefone": telefone,
        "cliente": cliente,
        "ano_atual": date.today().year,
        "horarios": HORARIOS_ENTREGA_PUBLICA,
    })


@app.post("/solicitar-manutencao/continuar", response_class=HTMLResponse)
async def manutencao_publica_continuar(request: Request, db: Session = Depends(get_db)):
    try:
        form = dict(await request.form())
    except ClientDisconnect:
        return RedirectResponse("/solicitar-manutencao", status_code=303)
    telefone = limpar_telefone(form.get("telefone") or "")
    cliente = cliente_por_whatsapp(db, telefone)
    if not cliente:
        return RedirectResponse("/solicitar-manutencao", status_code=303)
    return renderizar_equipamentos_publicos(request, db, cliente, telefone)


@app.post("/solicitar-manutencao/revisar-dados", response_class=HTMLResponse)
async def manutencao_publica_revisar_dados(request: Request, db: Session = Depends(get_db)):
    try:
        form = dict(await request.form())
    except ClientDisconnect:
        return RedirectResponse("/solicitar-manutencao", status_code=303)

    telefone_original = limpar_telefone(form.get("telefone_original") or form.get("telefone") or "")
    cliente = cliente_por_whatsapp(db, telefone_original)
    if not cliente:
        return RedirectResponse("/solicitar-manutencao", status_code=303)

    nome = limpar_nome_cliente(form.get("nome") or "")
    documento = limpar_documento(form.get("documento") or "")
    telefone_novo = limpar_telefone(form.get("telefone") or "")
    email = (form.get("email") or "").strip()
    obrigatorios = {
        "nome completo": nome, "CPF": documento, "WhatsApp": telefone_novo, "e-mail": email,
        "CEP": (form.get("cep") or "").strip(), "endereço": (form.get("endereco") or "").strip(),
        "número": (form.get("endereco_numero") or "").strip(), "bairro": (form.get("bairro") or "").strip(),
        "município": (form.get("municipio") or "").strip(), "estado": (form.get("estado") or "").strip(),
    }
    faltantes = [rotulo for rotulo, valor in obrigatorios.items() if not valor]
    erro = ""
    if faltantes:
        erro = "Preencha os campos obrigatórios: " + ", ".join(faltantes) + "."
    elif not cpf_valido(documento):
        erro = "Informe um CPF válido."
    elif not telefone_valido(telefone_novo):
        erro = "Informe um WhatsApp válido com 11 dígitos, incluindo DDD."
    elif db.query(Cliente).filter(Cliente.documento == documento, Cliente.id != cliente.id).first():
        erro = "Este CPF já pertence a outro cadastro."
    elif db.query(Cliente).filter(Cliente.telefone == telefone_novo, Cliente.id != cliente.id).first():
        erro = "Este WhatsApp já pertence a outro cadastro."

    if erro:
        for campo, valor in form.items():
            if hasattr(cliente, campo) and campo not in ("id",):
                setattr(cliente, campo, valor)
        cliente.nome = nome
        cliente.documento = documento
        cliente.telefone = telefone_novo
        return templates.TemplateResponse("organiza/manutencao_publica.html", {
            "request": request, "etapa": "revisar_dados", "erro": erro,
            "telefone": telefone_original, "cliente": cliente, "ano_atual": date.today().year,
            "horarios": HORARIOS_ENTREGA_PUBLICA,
        }, status_code=400)

    cliente.nome = nome
    cliente.documento = documento
    cliente.telefone = telefone_novo
    cliente.email = email
    cliente.empresa = (form.get("empresa") or "").strip() or None
    cliente.cep = (form.get("cep") or "").strip()
    cliente.municipio = (form.get("municipio") or "").strip()
    cliente.cidade = cliente.municipio
    cliente.estado = (form.get("estado") or "").strip().upper()
    cliente.endereco = (form.get("endereco") or "").strip()
    cliente.endereco_numero = (form.get("endereco_numero") or "").strip()
    cliente.complemento = (form.get("complemento") or "").strip() or None
    cliente.bairro = (form.get("bairro") or "").strip()
    db.commit()
    db.refresh(cliente)

    return renderizar_equipamentos_publicos(request, db, cliente, telefone_novo)


@app.post("/solicitar-manutencao/criar", response_class=HTMLResponse)
async def manutencao_publica_criar(request: Request, db: Session = Depends(get_db)):
    try:
        raw_form = await request.form()
    except ClientDisconnect:
        return RedirectResponse("/solicitar-manutencao?erro=Conexão interrompida. Tente novamente.", status_code=303)
    form = dict(raw_form)
    telefone = limpar_telefone(form.get("telefone") or "")
    cliente = cliente_por_whatsapp(db, telefone)
    if not cliente:
        return RedirectResponse("/solicitar-manutencao", status_code=303)

    selecionados = [int(v) for v in raw_form.getlist("equipamento_id") if str(v).isdigit()]

    tipo_atendimento = (form.get("tipo_atendimento") or "loja").strip().lower()
    if tipo_atendimento not in ("loja", "online"):
        tipo_atendimento = "loja"
    data_texto = (form.get("data_entrega") or "").strip()
    hora_texto = (form.get("hora_entrega") or "").strip()
    erro = ""
    data_entrega = None

    try:
        data_entrega = datetime.strptime(data_texto, "%Y-%m-%d").date()
    except ValueError:
        erro = "Informe uma data válida para a entrega."

    if not erro and (data_entrega < date.today() or data_entrega.weekday() > 4):
        erro = "A entrega deve ser agendada de segunda a sexta-feira."
    if not erro and hora_texto not in horarios_atendimento(tipo_atendimento):
        erro = "Escolha um horário válido: loja das 14:00 às 17:00 ou WhatsApp/online das 11:00 às 17:00."
    if not erro and not selecionados:
        erro = "Selecione pelo menos um equipamento."

    equipamentos_validos = db.query(Equipamento).filter(
        Equipamento.cliente_id == cliente.id,
        Equipamento.id.in_(selecionados or [-1]),
    ).all()
    if not erro and len(equipamentos_validos) != len(set(selecionados)):
        erro = "Um dos equipamentos selecionados não pertence ao cadastro informado."

    if not erro:
        for equipamento in equipamentos_validos:
            if equipamento_com_manutencao_aberta(db, equipamento.id):
                erro = f"O equipamento {equipamento.tipo or 'Equipamento'} {equipamento.modelo or ''} já possui uma manutenção em aberto."
                break
            descricao = (form.get(f"descricao_{equipamento.id}") or "").strip()
            if not descricao:
                erro = f"Descreva o problema do equipamento {equipamento.tipo or ''} {equipamento.modelo or ''}."
                break

    if erro:
        return renderizar_equipamentos_publicos(request, db, cliente, telefone, erro, form, 400)

    entrega_em = datetime.combine(data_entrega, datetime.strptime(hora_texto, "%H:%M").time())
    if horario_atendimento_ocupado(db, entrega_em):
        return renderizar_equipamentos_publicos(request, db, cliente, telefone, "Este horário já está reservado. Escolha outro horário disponível.", form, 409)
    criadas = []
    for equipamento in equipamentos_validos:
        manutencao = Manutencao(
            cliente_id=cliente.id,
            equipamento_id=equipamento.id,
            defeito=(form.get(f"descricao_{equipamento.id}") or "").strip(),
            entrega_prevista_em=entrega_em,
            tipo_atendimento=tipo_atendimento,
            status="Aguardando equipamento",
            observacao="Solicitação criada pelo link público.",
        )
        db.add(manutencao)
        db.flush()
        db.add(Orcamento(
            manutencao_id=manutencao.id,
            versao=1,
            token=secrets.token_urlsafe(24),
            status="Rascunho",
        ))
        criadas.append(manutencao)
    db.commit()

    return templates.TemplateResponse("organiza/manutencao_publica.html", {
        "request": request,
        "etapa": "concluido",
        "erro": "",
        "telefone": telefone,
        "cliente": cliente,
        "criadas": criadas,
        "data_entrega": entrega_em,
        "tipo_atendimento": tipo_atendimento,
        "horarios": horarios_atendimento(tipo_atendimento),
    })
