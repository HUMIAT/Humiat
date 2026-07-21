from urllib.parse import quote_plus
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
import urllib.request
import urllib.error
from datetime import date, datetime, time, timedelta
from typing import Optional
from urllib.parse import quote

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import ClientDisconnect
from sqlalchemy import Column, Date, DateTime, ForeignKey, Integer, String, Text, Float, func, or_, inspect, text
from sqlalchemy.orm import Session, relationship, selectinload

from config import ADMIN_NOME, ADMIN_SENHA, CHAVE_SESSAO, ORGANIZA_VERSAO, PUBLIC_BASE_URL
from database import Base, SessionLocal, engine, get_db
from services.comunicacao import (
    ComunicacaoService, PAISES, formatar_telefone as formatar_telefone_internacional,
    normalizar_contato, numero_internacional, telefone_valido as telefone_internacional_valido,
)

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


class ConfiguracaoSistema(Base):
    __tablename__ = "configuracoes_sistema"
    id = Column(Integer, primary_key=True)
    chave = Column(String(80), unique=True, nullable=False)
    valor = Column(String(120), nullable=True)
    atualizado_em = Column(DateTime, server_default=func.now(), onupdate=func.now())




class Cliente(Base):
    __tablename__ = "clientes"
    id = Column(Integer, primary_key=True)
    nome = Column(String(140), nullable=False)
    telefone = Column(String(20), nullable=False)
    pais = Column(String(2), nullable=False, default="BR")
    ddi = Column(String(5), nullable=False, default="55")
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

    def whatsapp_completo(self):
        return numero_internacional(self)

    def telefone_formatado(self):
        return formatar_telefone_internacional(self.pais, self.telefone)


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


class AgendaManual(Base):
    __tablename__ = "agenda_manual"
    id = Column(Integer, primary_key=True)
    titulo = Column(String(180), nullable=False)
    tipo = Column(String(40), nullable=False, default="visita")
    data_hora = Column(DateTime, nullable=False)
    contato = Column(String(120), nullable=True)
    observacao = Column(Text, nullable=True)
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
    comunicado = Column(Integer, nullable=False, default=0)
    ultima_comunicacao_em = Column(DateTime, nullable=True)
    ultima_comunicacao_tipo = Column(String(30), nullable=True)
    criado_em = Column(DateTime, server_default=func.now())
    cliente = relationship("Cliente")
    equipamento = relationship("Equipamento")
    orcamentos = relationship("Orcamento", back_populates="manutencao", cascade="all, delete-orphan")


class HistoricoComunicacao(Base):
    __tablename__ = "historico_comunicacoes"
    id = Column(Integer, primary_key=True)
    manutencao_id = Column(Integer, ForeignKey("assistencias.id"), nullable=True)
    cliente_id = Column(Integer, ForeignKey("clientes.id"), nullable=False)
    usuario_id = Column(Integer, ForeignKey("usuarios.id"), nullable=True)
    tipo = Column(String(30), nullable=False, default="GERAL")
    status = Column(String(30), nullable=False, default="ENVIADO")
    mensagem = Column(Text, nullable=True)
    enviado_em = Column(DateTime, nullable=False, default=datetime.now)
    manutencao = relationship("Manutencao")
    cliente = relationship("Cliente")
    usuario = relationship("Usuario")


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
    banco = Column(String(120), nullable=True)
    observacao = Column(Text, nullable=True)
    criado_em = Column(DateTime, server_default=func.now())
    orcamento = relationship("Orcamento", back_populates="pagamentos")


class PagamentoVenda(Base):
    """Recebimentos de venda. Uma venda pode ter vários pagamentos, bancos e datas."""
    __tablename__ = "venda_pagamentos"
    id = Column(Integer, primary_key=True)
    equipamento_id = Column(Integer, ForeignKey("equipamentos.id"), nullable=False, index=True)
    data = Column(Date, nullable=False, default=date.today)
    valor = Column(Float, nullable=False)
    banco = Column(String(120), nullable=False)
    forma = Column(String(40), nullable=True)
    observacao = Column(Text, nullable=True)
    criado_em = Column(DateTime, server_default=func.now())
    atualizado_em = Column(DateTime, server_default=func.now(), onupdate=func.now())
    equipamento = relationship("Equipamento")


class IntegracaoConect(Base):
    """Controle idempotente do que já foi enviado ao Connect."""
    __tablename__ = "integracao_conect"
    id = Column(Integer, primary_key=True)
    origem = Column(String(30), nullable=False)  # venda | manutencao
    registro_id = Column(Integer, nullable=False)
    id_externo = Column(String(120), unique=True, nullable=False, index=True)
    hash_conteudo = Column(String(64), nullable=True)
    enviado_em = Column(DateTime, nullable=True)
    resposta = Column(Text, nullable=True)
    # Quando marcado, o lançamento permanece no histórico, mas não entra em envios automáticos.
    ignorado = Column(Integer, nullable=False, default=0)


def limpar_telefone(valor: str) -> str:
    return re.sub(r"\D", "", valor or "")


def telefone_valido(valor: str, pais: str = "BR", ddi: str = "55") -> bool:
    return telefone_internacional_valido(pais, ddi, valor)


def formatar_telefone(valor: str, pais: str = "BR") -> str:
    return formatar_telefone_internacional(pais, valor)


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
templates.env.globals["PAISES"] = PAISES
templates.env.globals["whatsapp_url"] = ComunicacaoService.url_whatsapp
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
    """Fonte única de verdade para a etapa operacional.

    Um equipamento só entra em execução depois de:
    1) ter sido recebido/iniciado;
    2) ter orçamento aprovado;
    3) possuir pagamento registrado;
    4) possuir prazo;
    5) ter a confirmação de prazo enviada.
    """
    status = (m.status or "").strip()
    if status in {"Encerrada", "Cancelada"} or m.entregue_em:
        return 7
    if status in {"Pronto para retirada", "Retirada agendada"} or m.retirada_em or m.pronto_em:
        return 6

    o = sorted(m.orcamentos, key=lambda x: x.versao)[-1] if m.orcamentos else None
    aprovado = bool(o and (
        o.status in ("Aprovado", "Aprovado parcialmente", "Aprovado manualmente")
        or (o.status or "").startswith("Aprovado:")
    ))
    recebido = bool(m.recebido_em)

    # Sem recebimento/início do atendimento, permanece na entrada.
    if not recebido:
        return 1

    if aprovado:
        recebido_valor = sum(float(p.valor or 0) for p in o.pagamentos) if o else 0
        pronto_execucao = bool(recebido_valor > 0 and (m.prazo or "").strip() and m.confirmacao_prazo_em)
        if pronto_execucao:
            return 5
        return 4

    if o and o.status in ("Enviado", "Aguardando aprovação"):
        return 3
    return 2

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
templates.env.globals["rotulo_maquina"] = rotulo_maquina
templates.env.globals["codigo_tecnico"] = codigo_tecnico


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
            if "comunicado" not in existentes:
                conn.execute(text("ALTER TABLE assistencias ADD COLUMN comunicado INTEGER NOT NULL DEFAULT 0"))
            if "ultima_comunicacao_em" not in existentes:
                conn.execute(text(f"ALTER TABLE assistencias ADD COLUMN ultima_comunicacao_em {tipo_dt}"))
            if "ultima_comunicacao_tipo" not in existentes:
                conn.execute(text("ALTER TABLE assistencias ADD COLUMN ultima_comunicacao_tipo VARCHAR(30)"))
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
    if "assistencia_pagamentos" in insp.get_table_names():
        existentes_pagamentos = {c["name"] for c in insp.get_columns("assistencia_pagamentos")}
        with engine.begin() as conn:
            if "banco" not in existentes_pagamentos:
                conn.execute(text("ALTER TABLE assistencia_pagamentos ADD COLUMN banco VARCHAR(120)"))
    if "integracao_conect" in insp.get_table_names():
        existentes_integracao = {c["name"] for c in insp.get_columns("integracao_conect")}
        with engine.begin() as conn:
            if "ignorado" not in existentes_integracao:
                conn.execute(text("ALTER TABLE integracao_conect ADD COLUMN ignorado INTEGER NOT NULL DEFAULT 0"))
    if "clientes" in insp.get_table_names():
        existentes_clientes = {c["name"] for c in insp.get_columns("clientes")}
        with engine.begin() as conn:
            if "inscricao_estadual" not in existentes_clientes:
                conn.execute(text("ALTER TABLE clientes ADD COLUMN inscricao_estadual VARCHAR(30)"))
            if "pais" not in existentes_clientes:
                conn.execute(text("ALTER TABLE clientes ADD COLUMN pais VARCHAR(2) NOT NULL DEFAULT 'BR'"))
            if "ddi" not in existentes_clientes:
                conn.execute(text("ALTER TABLE clientes ADD COLUMN ddi VARCHAR(5) NOT NULL DEFAULT '55'"))
            conn.execute(text("UPDATE clientes SET pais = 'BR' WHERE pais IS NULL OR pais = ''"))
            conn.execute(text("UPDATE clientes SET ddi = '55' WHERE ddi IS NULL OR ddi = ''"))
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
            cliente_existente.pais, cliente_existente.ddi, cliente_existente.telefone = normalizar_contato(
                cliente_existente.pais, cliente_existente.ddi, cliente_existente.telefone
            )

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


def _contexto_operacao(db: Session):
    manutencoes = _manutencoes_operacao(db)
    filas = {
        "atendimento": [], "orcamentos": [], "comunicar_orcamentos": [],
        "aprovacoes": [], "pagamentos": [], "execucao": [],
        "pausados": [], "prontos": [], "retiradas": [],
    }
    for manutencao in manutencoes:
        chave = _fila_operacional_exclusiva(manutencao)
        if chave:
            filas[chave].append(manutencao)

    # Datas úteis para leitura rápida nos cards da operação.
    for lista in filas.values():
        for m in lista:
            if m.retirada_em:
                m.operacao_data = m.retirada_em
            elif m.entrega_prevista_em:
                m.operacao_data = m.entrega_prevista_em
            elif m.pronto_em:
                m.operacao_data = m.pronto_em
            elif m.recebido_em:
                m.operacao_data = m.recebido_em
            else:
                m.operacao_data = m.criado_em

    grupos = {chave: _agrupar_por_cliente(valor) for chave, valor in filas.items()}
    return {
        "filas": filas,
        "grupos": grupos,
        # A Central trabalha por cliente. O número do card representa grupos de ação,
        # enquanto a quantidade de equipamentos continua visível dentro de cada grupo.
        "contagens": {chave: len(valor) for chave, valor in grupos.items()},
        "contagens_equipamentos": {chave: len(valor) for chave, valor in filas.items()},
        "total_operacao": sum(len(valor) for valor in grupos.values()),
        "total_manutencoes": len(manutencoes),
    }


@app.get("/organiza", response_class=HTMLResponse)
def painel(
    request: Request,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    """Dashboard resumido do Organiza.

    Os cards encaminham para a Central Operacional já com o filtro correto.
    """
    contexto = _contexto_operacao(db)
    fases = [
        {"numero": "1", "nome": "Entrada", "chaves": ["atendimento"], "filtro": "atendimento"},
        {"numero": "2", "nome": "Orçamento", "chaves": ["orcamentos", "comunicar_orcamentos"], "filtro": "orcamentos"},
        {"numero": "3", "nome": "Aceite", "chaves": ["aprovacoes"], "filtro": "aprovacoes"},
        {"numero": "4", "nome": "Pagamento", "chaves": ["pagamentos"], "filtro": "pagamentos"},
        {"numero": "5", "nome": "Execução", "chaves": ["execucao", "pausados", "prontos"], "filtro": "execucao"},
        {"numero": "6", "nome": "Retirada", "chaves": ["retiradas"], "filtro": "retiradas"},
    ]
    for fase in fases:
        fase["quantidade"] = sum(contexto["contagens_equipamentos"].get(chave, 0) for chave in fase["chaves"])
    return templates.TemplateResponse("organiza/painel.html", {
        "request": request, "usuario": usuario, "fases": fases,
        "total_operacao": contexto["total_manutencoes"],
        "total_clientes_operacao": contexto["total_operacao"],
    })


@app.get("/organiza/central", response_class=HTMLResponse)
def central_operacional(
    request: Request,
    etapa: str = "todos",
    busca: str = "",
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    """Central Operacional: filtro, ações da etapa e seleção individual ou em lote."""
    contexto = _contexto_operacao(db)
    etapas_meta = {
        "atendimento": ("1", "Entrada", "Aguardando atendimento ou entrega"),
        "orcamentos": ("2", "Orçamento", "Fazer orçamento"),
        "comunicar_orcamentos": ("3", "Comunicar", "Comunicar orçamento"),
        "aprovacoes": ("4", "Aceite", "Aguardando aprovação"),
        "pagamentos": ("5", "Pagamento", "Pagamento, prazo e confirmação"),
        "execucao": ("6", "Execução", "Em execução"),
        "pausados": ("7", "Peças", "Aguardando item / peça"),
        "prontos": ("8", "Pronto", "Comunicar equipamento pronto"),
        "retiradas": ("9", "Retirada", "Aguardando retirada"),
    }
    etapa = etapa if etapa in etapas_meta or etapa == "todos" else "todos"
    termo = (busca or "").strip().lower()
    selecionadas = []
    for chave, lista in contexto["filas"].items():
        if etapa != "todos" and chave != etapa:
            continue
        numero, nome_curto, titulo = etapas_meta[chave]
        for m in lista:
            texto_busca = " ".join([
                getattr(m.cliente, "nome", "") or "",
                rotulo_maquina(m.equipamento) if m.equipamento else "",
                getattr(m.equipamento, "tipo", "") or "",
                getattr(m.equipamento, "modelo", "") or "",
                getattr(m, "defeito", "") or "",
                str(m.id),
            ]).lower()
            if termo and termo not in texto_busca:
                continue
            m.central_chave = chave
            m.central_numero = numero
            m.central_nome_curto = nome_curto
            m.central_titulo = titulo
            selecionadas.append(m)

    central_grupos = _agrupar_por_cliente(selecionadas)
    contexto.update({
        "request": request, "usuario": usuario, "pagina_inicial": False,
        "etapa_filtro": etapa, "busca": busca, "etapas_meta": etapas_meta,
        "central_grupos": central_grupos, "central_total": len(selecionadas),
    })
    return templates.TemplateResponse("organiza/operacao.html", contexto)


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
    cliente.pais, cliente.ddi, cliente.telefone = normalizar_contato(
        form.get("pais") or getattr(cliente, "pais", "BR"),
        form.get("ddi") or getattr(cliente, "ddi", "55"),
        form.get("telefone") or "",
    )
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
    pais, ddi, telefone = normalizar_contato(form.get("pais"), form.get("ddi"), form.get("telefone"))
    erro = ""
    if not (form.get("nome") or "").strip():
        erro = "Informe o nome do cliente."
    elif not telefone_valido(telefone, pais, ddi):
        erro = "Informe um WhatsApp válido para o país selecionado."
    elif db.query(Cliente).filter(Cliente.ddi == ddi, Cliente.telefone == telefone).first():
        erro = "Já existe um cliente com este WhatsApp."
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
    pais, ddi, telefone = normalizar_contato(form.get("pais"), form.get("ddi"), form.get("telefone"))
    erro = ""
    if not (form.get("nome") or "").strip(): erro = "Informe o nome do cliente."
    elif not telefone_valido(telefone, pais, ddi): erro = "Informe um WhatsApp válido para o país selecionado."
    elif db.query(Cliente).filter(Cliente.ddi == ddi, Cliente.telefone == telefone, Cliente.id != cliente_id).first(): erro = "Já existe outro cliente com este WhatsApp."
    if erro:
        preencher_cliente(cliente, form)
        return templates.TemplateResponse("organiza/cliente_form.html", {"request": request, "usuario": usuario, "cliente": cliente, "erro": erro}, status_code=400)
    preencher_cliente(cliente, form); db.commit()
    return RedirectResponse(f"/organiza/clientes/{cliente.id}", status_code=303)


PACOTE_ATUAL_PADRAO = "2026.1"


def obter_pacote_atual(db: Session) -> str:
    configuracao = db.query(ConfiguracaoSistema).filter(
        ConfiguracaoSistema.chave == "pacote_atual"
    ).first()
    valor = (configuracao.valor if configuracao else PACOTE_ATUAL_PADRAO) or PACOTE_ATUAL_PADRAO
    valor = valor.strip()
    return valor if re.fullmatch(r"\d{4}\.[12]", valor) else PACOTE_ATUAL_PADRAO


def calcular_falta_pacote(pacote: str | None, pacote_atual: str = PACOTE_ATUAL_PADRAO) -> int | None:
    """Calcula quantas atualizações semestrais faltam até o pacote atual cadastrado."""
    valor = (pacote or "").strip().upper()
    atual = re.fullmatch(r"(\d{4})\.([12])", pacote_atual)
    informado = re.fullmatch(r"(\d{4})\.([12])", valor)
    if not atual or not informado:
        return None

    ano_atual, semestre_atual = map(int, atual.groups())
    ano_pacote, semestre_pacote = map(int, informado.groups())
    indice_atual = ano_atual * 2 + semestre_atual
    indice_pacote = ano_pacote * 2 + semestre_pacote
    return max(indice_atual - indice_pacote, 0)


def preencher_equipamento(eq: Equipamento, form: dict, db: Session):
    eq.tipo = tipo_equipamento_padrao((form.get("tipo") or "").strip()) or None
    eq.modelo = (form.get("modelo") or "").strip() or None
    pacote_informado = (form.get("pacote") or "").strip()
    if pacote_informado:
        eq.pacote = pacote_informado
    elif not eq.pacote:
        eq.pacote = None
    # Este valor é derivado do pacote instalado e nunca é informado manualmente.
    eq.falta_pacote = calcular_falta_pacote(eq.pacote, obter_pacote_atual(db))
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

    # A faixa histórica KRJ00001..KRJ00040 só é protegida na EDIÇÃO.
    # Cadastros novos devem aceitar normalmente a sequência automática atual
    # (ex.: KRJ00671, KRJ00672...), sem limitar o número a 40.
    if anterior and codigo != anterior:
        numero_anterior = int(anterior[3:]) if re.fullmatch(r"KRJ\d{5}", anterior) else None
        numero_novo = int(codigo[3:])

        # Equipamentos antigos/reservados devem permanecer dentro da faixa histórica
        # quando o código técnico for alterado manualmente.
        if numero_anterior is not None and 1 <= numero_anterior <= 40 and not 1 <= numero_novo <= 40:
            return (
                "Este equipamento utiliza um código histórico entre "
                "KRJ00001 e KRJ00040. Ao editar o código técnico, mantenha-o nessa faixa."
            )

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
    pacotes_bd = [x[0] for x in db.query(Equipamento.pacote).filter(Equipamento.pacote.isnot(None), Equipamento.pacote != "").distinct().all()]
    tipos = list(dict.fromkeys(["JUKEBOX", "MALETA", "IPHONE", "FLIPERAMA"] + tipos_bd))

    pacote_atual = obter_pacote_atual(db)
    atual = tuple(map(int, pacote_atual.split(".")))
    pacotes_validos = []
    especiais = []
    for pacote in pacotes_bd:
        valor = (pacote or "").strip()
        correspondencia = re.fullmatch(r"(\d{4})\.([12])", valor)
        if correspondencia:
            chave = tuple(map(int, correspondencia.groups()))
            if chave <= atual:
                pacotes_validos.append(valor)
        elif valor.upper() in {"NE", "NA"}:
            especiais.append(valor.upper())

    pacotes_validos = sorted(set(pacotes_validos + [pacote_atual]), key=lambda valor: tuple(map(int, valor.split("."))), reverse=True)
    pacotes = pacotes_validos + sorted(set(especiais))
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
        "pacote_atual": obter_pacote_atual(db),
    })


@app.post("/organiza/clientes/{cliente_id}/equipamentos/novo")
async def equipamento_criar(cliente_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    cliente = db.query(Cliente).filter(Cliente.id == cliente_id).first()
    if not cliente: raise HTTPException(404)
    form = dict(await request.form())
    if not (form.get("tipo") or "").strip():
        eq = Equipamento(cliente_id=cliente_id); preencher_equipamento(eq, form, db)
        tipos, pacotes = opcoes_equipamentos(db)
        return templates.TemplateResponse("organiza/equipamento_form.html", {
            "request": request, "usuario": usuario, "cliente": cliente, "equipamento": eq,
            "erro": "Informe o tipo do equipamento.", "tipos": tipos, "pacotes": pacotes,
            "proxima_maquina": proximo_codigo_maquina(db),
            "proximo_numero_cliente": proximo_numero_cliente(db, cliente_id),
        }, status_code=400)
    eq = Equipamento(cliente_id=cliente_id); preencher_equipamento(eq, form, db)
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
    preencher_equipamento(eq, form, db)
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
    preencher_equipamento(eq, form, db)
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


def _migrar_pagamentos_legados_vendas(db: Session, equipamentos: list[Equipamento]) -> int:
    """Converte recebimentos legados sem executar uma consulta por equipamento."""
    candidatos = [eq for eq in equipamentos if round(moeda_num(eq.pago), 2) > 0.009]
    if not candidatos:
        return 0

    ids = [eq.id for eq in candidatos]
    existentes = {
        equipamento_id for (equipamento_id,) in
        db.query(PagamentoVenda.equipamento_id)
        .filter(PagamentoVenda.equipamento_id.in_(ids))
        .distinct().all()
    }
    alterados = 0
    for eq in candidatos:
        if eq.id in existentes:
            continue
        pagamento = PagamentoVenda(
            equipamento_id=eq.id,
            data=eq.data_compra or eq.previsao_entrega or date.today(),
            valor=round(moeda_num(eq.pago), 2),
            banco="Histórico",
            forma="Histórico",
            observacao=_obs_pagamento_padrao(eq, eq.cliente, "Saldo recebido antes do controle detalhado"),
        )
        db.add(pagamento)
        db.flush()
        db.add(IntegracaoConect(
            origem="venda", registro_id=pagamento.id,
            id_externo=f"ORGANIZA-VENDA-PAG-{pagamento.id}", ignorado=1,
            resposta="Pagamento histórico migrado do campo legado pago; não enviar ao Connect.",
        ))
        alterados += 1
    if alterados:
        db.commit()
    return alterados


@app.get("/organiza/vendas", response_class=HTMLResponse)
def vendas(request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    equipamentos = (
        db.query(Equipamento)
        .options(selectinload(Equipamento.cliente))
        .filter(or_(
            Equipamento.data_compra.isnot(None),
            Equipamento.previsao_entrega.isnot(None),
            Equipamento.valor.isnot(None),
            Equipamento.pago.isnot(None),
            Equipamento.status.in_(STATUS_VENDA),
        ))
        .all()
    )
    equipamentos = [eq for eq in equipamentos if equipamento_eh_venda(eq)]
    status_opcoes = sorted({eq.status for eq in equipamentos if eq.status})

    # Recupera automaticamente os recebimentos antigos que existiam apenas no
    # campo legado `pago`. Esses lançamentos ficam locais e NUNCA vão ao Connect.
    # O financeiro exibido na listagem é sempre recalculado pelos pagamentos
    # reais, evitando depender de campos legados pago/falta desatualizados.
    ids = [eq.id for eq in equipamentos]
    pagamentos_por_equipamento = {}
    if ids:
        for p in db.query(PagamentoVenda).filter(PagamentoVenda.equipamento_id.in_(ids)).all():
            pagamentos_por_equipamento.setdefault(p.equipamento_id, 0.0)
            pagamentos_por_equipamento[p.equipamento_id] += float(p.valor or 0)

    for eq in equipamentos:
        total = moeda_num(eq.valor)
        recebido = round(pagamentos_por_equipamento.get(eq.id, 0.0), 2)
        eq.total_calculado = total
        eq.recebido_calculado = recebido
        eq.falta_calculada = max(round(total - recebido, 2), 0)
        eq.excesso_calculado = max(round(recebido - total, 2), 0)

    q = (request.query_params.get("q") or "").strip().lower()
    pagamento = (request.query_params.get("pagamento") or "todos").strip()
    valor_filtro = (request.query_params.get("valor") or "todos").strip()
    status = (request.query_params.get("status") or "todos").strip()
    ordem = (request.query_params.get("ordem") or "recentes").strip()

    if q:
        equipamentos = [eq for eq in equipamentos if q in " ".join([
            eq.cliente.nome or "", eq.tipo or "", eq.modelo or "",
            codigo_tecnico(eq), rotulo_maquina(eq),
        ]).lower()]
    if pagamento == "pendente":
        equipamentos = [eq for eq in equipamentos if eq.falta_calculada > 0.009]
    elif pagamento == "quitado":
        equipamentos = [eq for eq in equipamentos if eq.total_calculado > 0 and eq.falta_calculada <= 0.009 and eq.excesso_calculado <= 0.009]
    elif pagamento == "sem_pagamento":
        equipamentos = [eq for eq in equipamentos if eq.recebido_calculado <= 0.009]
    elif pagamento == "excesso":
        equipamentos = [eq for eq in equipamentos if eq.excesso_calculado > 0.009]

    if valor_filtro == "acima_5000":
        equipamentos = [eq for eq in equipamentos if eq.total_calculado > 5000]
    if status != "todos":
        equipamentos = [eq for eq in equipamentos if (eq.status or "") == status]

    if ordem == "antigos":
        equipamentos.sort(key=lambda eq: (eq.data_compra or date.min, eq.criado_em or datetime.min, eq.id))
    elif ordem == "maior_valor":
        equipamentos.sort(key=lambda eq: (eq.total_calculado, eq.data_compra or date.min, eq.id), reverse=True)
    elif ordem == "maior_saldo":
        equipamentos.sort(key=lambda eq: (eq.falta_calculada, eq.data_compra or date.min, eq.id), reverse=True)
    else:
        equipamentos.sort(key=lambda eq: (eq.data_compra or date.min, eq.criado_em or datetime.min, eq.id), reverse=True)

    # Paginação: não renderizar centenas de cards em uma única resposta.
    total_vendas = len(equipamentos)
    por_pagina = 50
    try:
        pagina = max(int(request.query_params.get("pagina") or 1), 1)
    except (TypeError, ValueError):
        pagina = 1
    total_paginas = max((total_vendas + por_pagina - 1) // por_pagina, 1)
    pagina = min(pagina, total_paginas)
    inicio = (pagina - 1) * por_pagina
    equipamentos = equipamentos[inicio:inicio + por_pagina]

    return templates.TemplateResponse("organiza/vendas.html", {
        "request": request, "usuario": usuario, "vendas": equipamentos,
        "q": request.query_params.get("q", ""), "pagamento_filtro": pagamento,
        "valor_filtro": valor_filtro, "status_filtro": status, "ordem": ordem,
        "status_opcoes": status_opcoes, "total_vendas": total_vendas,
        "pagina": pagina, "total_paginas": total_paginas,
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
    preencher_equipamento(eq, form, db)
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

    logo_path = os.path.join(os.path.dirname(__file__), "static", "img", "logo-karaoke-rj.png")
    if not os.path.exists(logo_path):
        logo_path = os.path.join(os.path.dirname(__file__), "static", "img", "karaoke-rj-garantia.jpeg")
    logo = Image(logo_path, width=30 * mm, height=22 * mm) if os.path.exists(logo_path) else Spacer(30 * mm, 22 * mm)
    dados_empresa = Paragraph(
        "<b>KARAOKE &amp; GAMES RJ</b><br/>CNPJ: 35.458.112/0001-75 · IM: 1213508-4<br/>"
        "Rua João Romariz, 313 - Ramos - Rio de Janeiro/RJ - CEP: 21031-700<br/>"
        "WhatsApp: (21) 99507-9690 / (21) 99650-4516<br/>www.karaokerj.com.br · contato@karaokerj.com.br", cabecalho
    )
    header = Table([[logo, dados_empresa]], colWidths=[35 * mm, 145 * mm])
    header.setStyle(TableStyle([("VALIGN", (0,0), (-1,-1), "MIDDLE"), ("LINEBELOW", (0,0), (-1,-1), 0.8, colors.HexColor("#555555")), ("LEFTPADDING", (0,0), (-1,-1), 0), ("RIGHTPADDING", (0,0), (-1,-1), 0), ("BOTTOMPADDING", (0,0), (-1,-1), 5)]))

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


@app.get("/organiza/configuracoes/pacotes", response_class=HTMLResponse)
def configuracao_pacotes(
    request: Request,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    exigir_admin(usuario)
    return templates.TemplateResponse(
        "organiza/configuracao_pacotes.html",
        {
            "request": request,
            "usuario": usuario,
            "pacote_atual": obter_pacote_atual(db),
            "mensagem": request.query_params.get("mensagem", ""),
            "erro": "",
        },
    )


@app.post("/organiza/configuracoes/pacotes", response_class=HTMLResponse)
async def configuracao_pacotes_salvar(
    request: Request,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    exigir_admin(usuario)
    form = dict(await request.form())
    pacote_atual = (form.get("pacote_atual") or "").strip()

    if not re.fullmatch(r"\d{4}\.[12]", pacote_atual):
        return templates.TemplateResponse(
            "organiza/configuracao_pacotes.html",
            {
                "request": request,
                "usuario": usuario,
                "pacote_atual": pacote_atual,
                "mensagem": "",
                "erro": "Informe o pacote no formato AAAA.1 ou AAAA.2.",
            },
            status_code=400,
        )

    configuracao = db.query(ConfiguracaoSistema).filter(
        ConfiguracaoSistema.chave == "pacote_atual"
    ).first()
    if not configuracao:
        configuracao = ConfiguracaoSistema(chave="pacote_atual")
        db.add(configuracao)
    configuracao.valor = pacote_atual

    for equipamento in db.query(Equipamento).all():
        equipamento.falta_pacote = calcular_falta_pacote(
            equipamento.pacote, pacote_atual
        )

    db.commit()
    return RedirectResponse(
        "/organiza/configuracoes/pacotes?mensagem=Pacote+atual+salvo+e+cálculos+atualizados.",
        status_code=303,
    )


@app.get("/organiza/usuarios", response_class=HTMLResponse)
def usuarios(request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    exigir_admin(usuario)
    return templates.TemplateResponse("organiza/usuarios.html", {"request": request, "usuario": usuario, "usuarios": db.query(Usuario).order_by(Usuario.nome).all()})


def moeda_num(valor) -> float:
    """Converte valores monetários sem perder casas decimais.

    Aceita tanto o padrão brasileiro (1.234,56) quanto valores internos/HTML
    com ponto decimal (1234.56). O parser anterior removia todo ponto e podia
    transformar 180.00 em 18.000,00.
    """
    from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

    texto = str(valor or "0").replace("R$", "").replace(" ", "").strip()
    if not texto:
        return 0.0

    # Quando há os dois separadores, o último indica as casas decimais.
    if "," in texto and "." in texto:
        if texto.rfind(",") > texto.rfind("."):
            texto = texto.replace(".", "").replace(",", ".")
        else:
            texto = texto.replace(",", "")
    elif "," in texto:
        texto = texto.replace(".", "").replace(",", ".")
    elif texto.count(".") > 1:
        partes = texto.split(".")
        if len(partes[-1]) in (1, 2):
            texto = "".join(partes[:-1]) + "." + partes[-1]
        else:
            texto = "".join(partes)
    elif texto.count(".") == 1:
        inteiro, decimal = texto.split(".", 1)
        # 5.000 é normalmente milhar em pt-BR; 2530.00 é decimal interno.
        if len(decimal) == 3:
            texto = inteiro + decimal

    try:
        numero = Decimal(texto).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return float(numero)
    except (InvalidOperation, ValueError):
        return 0.0


def totais_orcamento(orcamento: Orcamento):
    manutencao = max(float(orcamento.valor_manutencao or 0), 0)
    itens_obrigatorios = sum(i.preco_venda * i.quantidade for i in orcamento.itens if not i.opcional)
    obrigatorio = manutencao + itens_obrigatorios

    itens_opcionais = [i for i in orcamento.itens if i.opcional]
    opcionais = sum(i.preco_venda * i.quantidade for i in itens_opcionais)
    opcionais_aprovados = [i for i in itens_opcionais if i.aprovado]
    todos_opcionais_aprovados = len(opcionais_aprovados) == len(itens_opcionais)

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
def manutencoes_lista(request: Request, status: str = "orcamento", usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    """Lista usando a mesma regra operacional do painel.

    Evita divergência entre o número exibido no card e os registros encontrados
    ao abrir a etapa. Os aliases antigos continuam funcionando.
    """
    query = db.query(Manutencao).options(
        selectinload(Manutencao.cliente),
        selectinload(Manutencao.equipamento),
        selectinload(Manutencao.orcamentos).selectinload(Orcamento.itens),
        selectinload(Manutencao.orcamentos).selectinload(Orcamento.pagamentos),
    )
    lista = query.order_by(Manutencao.criado_em.desc()).all()

    etapas_por_filtro = {
        "entrada": 1,
        "orcamento": 2,
        "aceite": 3,
        "aprovacao": 3,
        "pagamento": 4,
        "producao": 5,
        "execucao": 5,
        "agenda": 6,
        "retirada": 6,
    }
    if status in etapas_por_filtro:
        etapa = etapas_por_filtro[status]
        lista = [m for m in lista if etapa_manutencao(m) == etapa]
    elif status == "encerradas":
        lista = [m for m in lista if etapa_manutencao(m) == 7]

    # Informações prontas para a interface, sem recalcular regras no template.
    for m in lista:
        m.etapa_operacional = etapa_manutencao(m)
        o = _orcamento_atual(m)
        m.orcamento_comunicado = bool(
            o and o.status in ("Enviado", "Aguardando aprovação")
        )

    return templates.TemplateResponse("organiza/manutencoes.html", {
        "request": request,
        "usuario": usuario,
        "manutencoes": lista,
        "filtro_status": status,
    })


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


@app.post("/organiza/manutencoes/{manutencao_id}/encerrar-pendente")
async def manutencao_encerrar_pendente(
    manutencao_id: int,
    request: Request,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    """Permite encerrar/cancelar uma manutenção sem deixar pendência antes da aprovação.

    A ação é permitida somente até a etapa de aceite (1, 2 ou 3) e bloqueada
    assim que houver orçamento aprovado, preservando a integridade financeira.
    """
    m = carregar_manutencao(db, manutencao_id)
    if not m:
        raise HTTPException(404)

    etapa = etapa_manutencao(m)
    orcamento = sorted(m.orcamentos, key=lambda x: x.versao)[-1] if m.orcamentos else None
    aprovado = bool(orcamento and (
        orcamento.status in ("Aprovado", "Aprovado parcialmente", "Aprovado manualmente")
        or (orcamento.status or "").startswith("Aprovado:")
    ))
    if etapa > 3 or aprovado:
        return RedirectResponse(
            f"/organiza/manutencoes/{m.id}?erro_encerramento=Após a aprovação do orçamento, use o fluxo normal da manutenção.",
            status_code=303,
        )

    form = await request.form()
    acao = (form.get("acao") or "cancelar").strip().lower()
    if acao == "finalizar":
        m.status = "Encerrada"
        m.entregue_em = m.entregue_em or datetime.now()
    else:
        # Não apaga fisicamente: remove das filas e preserva todo o histórico.
        m.status = "Cancelada"
    m.entrega_prevista_em = None
    m.retirada_em = None
    db.commit()

    destino = (form.get("destino") or "").strip()
    if destino.startswith("/organiza/"):
        return RedirectResponse(destino, status_code=303)
    return RedirectResponse("/organiza/manutencoes", status_code=303)


@app.get("/organiza/manutencoes/{manutencao_id}/relatorio.pdf")
def manutencao_relatorio_pdf(
    manutencao_id: int,
    mostrar_valores: int = 1,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    """Relatório técnico da manutenção, com opção de exibir ou ocultar valores."""
    m = carregar_manutencao(db, manutencao_id)
    if not m:
        raise HTTPException(404)
    orcamento = sorted(m.orcamentos, key=lambda x: x.versao)[-1] if m.orcamentos else None
    exibir_valores = bool(mostrar_valores)

    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    fonte_regular = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    fonte_negrito = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    nome_fonte, nome_fonte_bold = "Helvetica", "Helvetica-Bold"
    if os.path.exists(fonte_regular) and os.path.exists(fonte_negrito):
        nome_fonte, nome_fonte_bold = "DejaVu", "DejaVu-Bold"
        if nome_fonte not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(nome_fonte, fonte_regular))
            pdfmetrics.registerFont(TTFont(nome_fonte_bold, fonte_negrito))

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=14*mm, leftMargin=14*mm,
                            topMargin=12*mm, bottomMargin=14*mm,
                            title=f"Relatório de Manutenção #{m.id}")
    styles = getSampleStyleSheet()
    corpo = ParagraphStyle("RelatorioCorpo", parent=styles["BodyText"], fontName=nome_fonte, fontSize=9, leading=12)
    pequeno = ParagraphStyle("RelatorioPequeno", parent=corpo, fontSize=8, leading=10)
    titulo = ParagraphStyle("RelatorioTitulo", parent=styles["Title"], fontName=nome_fonte_bold, fontSize=14, leading=17, alignment=TA_CENTER)
    secao = ParagraphStyle("RelatorioSecao", parent=corpo, fontName=nome_fonte_bold, fontSize=10.5, leading=13, spaceBefore=7, spaceAfter=5)

    logo_path = os.path.join(os.path.dirname(__file__), "static", "img", "logo-karaoke-rj.png")
    if not os.path.exists(logo_path):
        logo_path = os.path.join(os.path.dirname(__file__), "static", "img", "karaoke-rj-garantia.jpeg")
    logo = Image(logo_path, width=30*mm, height=22*mm) if os.path.exists(logo_path) else Spacer(30*mm, 22*mm)
    empresa = Paragraph(
        "<b>KARAOKE &amp; GAMES RJ</b><br/>CNPJ: 35.458.112/0001-75 · IM: 1213508-4<br/>"
        "Rua João Romariz, 313 - Ramos - Rio de Janeiro/RJ - CEP: 21031-700<br/>"
        "WhatsApp: (21) 99507-9690 / (21) 99650-4516<br/>www.karaokerj.com.br · contato@karaokerj.com.br", pequeno)
    header = Table([[logo, empresa]], colWidths=[35*mm, 145*mm])
    header.setStyle(TableStyle([("VALIGN",(0,0),(-1,-1),"MIDDLE"),("LINEBELOW",(0,0),(-1,-1),0.8,colors.HexColor("#555555")),
                                ("LEFTPADDING",(0,0),(-1,-1),0),("RIGHTPADDING",(0,0),(-1,-1),0),("BOTTOMPADDING",(0,0),(-1,-1),5)]))

    def ptxt(v):
        return Paragraph(str(v or "-"), corpo)
    def dt(v):
        return v.strftime("%d/%m/%Y %H:%M") if v else "-"

    eq = m.equipamento
    cliente = m.cliente
    story = [header, Spacer(1,7), Paragraph(f"RELATÓRIO DE MANUTENÇÃO Nº {m.id}", titulo), Spacer(1,7)]
    dados = [
        [Paragraph("<b>Cliente</b>", corpo), ptxt(cliente.nome), Paragraph("<b>WhatsApp</b>", corpo), ptxt(formatar_telefone(cliente.telefone) if cliente.telefone else "-")],
        [Paragraph("<b>Equipamento</b>", corpo), ptxt(f"{rotulo_maquina(eq)} · {eq.tipo or ''} {eq.modelo or ''}".strip()), Paragraph("<b>Cód. técnico</b>", corpo), ptxt(codigo_tecnico(eq))],
        [Paragraph("<b>Entrada</b>", corpo), ptxt(dt(m.recebido_em or m.criado_em)), Paragraph("<b>Status</b>", corpo), ptxt(m.status)],
    ]
    tab = Table(dados, colWidths=[24*mm, 68*mm, 24*mm, 64*mm])
    tab.setStyle(TableStyle([("GRID",(0,0),(-1,-1),0.35,colors.HexColor("#bbbbbb")),("VALIGN",(0,0),(-1,-1),"TOP"),
                             ("BACKGROUND",(0,0),(0,-1),colors.HexColor("#f2f2f2")),("BACKGROUND",(2,0),(2,-1),colors.HexColor("#f2f2f2")),
                             ("LEFTPADDING",(0,0),(-1,-1),5),("RIGHTPADDING",(0,0),(-1,-1),5),("TOPPADDING",(0,0),(-1,-1),5),("BOTTOMPADDING",(0,0),(-1,-1),5)]))
    story += [tab, Paragraph("Problema informado", secao), ptxt(m.defeito), Paragraph("Diagnóstico técnico", secao), ptxt(m.diagnostico or "Não informado")]

    if orcamento:
        story.append(Paragraph("Serviços e itens", secao))
        if exibir_valores:
            linhas = [[ptxt("Descrição"), ptxt("Qtd."), ptxt("Valor unit."), ptxt("Total")]]
            if float(orcamento.valor_manutencao or 0) > 0:
                linhas.append([ptxt("Serviço de manutenção"), ptxt("1"), ptxt(formatar_moeda(orcamento.valor_manutencao)), ptxt(formatar_moeda(orcamento.valor_manutencao))])
            for item in orcamento.itens:
                linhas.append([ptxt(item.descricao + (" (opcional)" if item.opcional else "")), ptxt(item.quantidade), ptxt(formatar_moeda(item.preco_venda)), ptxt(formatar_moeda(item.preco_venda * item.quantidade))])
            tabela_itens = Table(linhas, colWidths=[92*mm, 16*mm, 34*mm, 38*mm], repeatRows=1)
        else:
            linhas = [[ptxt("Descrição"), ptxt("Qtd.")]]
            if float(orcamento.valor_manutencao or 0) > 0:
                linhas.append([ptxt("Serviço de manutenção"), ptxt("1")])
            for item in orcamento.itens:
                linhas.append([ptxt(item.descricao + (" (opcional)" if item.opcional else "")), ptxt(item.quantidade)])
            tabela_itens = Table(linhas, colWidths=[155*mm, 25*mm], repeatRows=1)
        tabela_itens.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),colors.HexColor("#eeeeee")),("FONTNAME",(0,0),(-1,0),nome_fonte_bold),
                                          ("GRID",(0,0),(-1,-1),0.35,colors.HexColor("#bbbbbb")),("VALIGN",(0,0),(-1,-1),"TOP"),
                                          ("LEFTPADDING",(0,0),(-1,-1),5),("RIGHTPADDING",(0,0),(-1,-1),5),("TOPPADDING",(0,0),(-1,-1),5),("BOTTOMPADDING",(0,0),(-1,-1),5)]))
        story.append(tabela_itens)
        if exibir_valores:
            totais = totais_orcamento(orcamento)
            story += [Spacer(1,5), Paragraph(f"<b>Total do orçamento: {formatar_moeda(totais.get('aprovado', totais.get('geral', 0)))}</b>", corpo)]
            if float(totais.get("desconto_informado", 0) or 0) > 0:
                story.append(Paragraph(f"Desconto informado: {formatar_moeda(totais['desconto_informado'])}", corpo))
        if orcamento.forma_pagamento_orcamento:
            story.append(Paragraph(f"<b>Condição de pagamento:</b> {orcamento.forma_pagamento_orcamento}", corpo))
        if orcamento.prazo_dias_uteis:
            story.append(Paragraph(f"<b>Prazo:</b> {orcamento.prazo_dias_uteis} dias úteis após a confirmação do pagamento", corpo))

    if m.observacao:
        story += [Paragraph("Observações", secao), ptxt(m.observacao)]
    story += [Spacer(1,18), Table([["____________________________________________"],["Responsável / Karaokê RJ"]], colWidths=[90*mm],
                                  style=TableStyle([("ALIGN",(0,0),(-1,-1),"CENTER"),("FONTNAME",(0,0),(-1,-1),nome_fonte),("FONTSIZE",(0,0),(-1,-1),8)]))]
    doc.build(story)
    nome = _nome_arquivo(cliente.nome)
    sufixo = "com_valores" if exibir_valores else "sem_valores"
    return Response(buffer.getvalue(), media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="manutencao_{m.id}_{nome}_{sufixo}.pdf"'})


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



@app.post("/organiza/manutencoes/{manutencao_id}/etapa-2/salvar")
async def manutencao_etapa2_salvar(
    manutencao_id: int,
    request: Request,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    """Salva os dados principais do orçamento de uma só vez.

    A inclusão/remoção de itens continua sendo uma ação de lista, mas valor da
    manutenção, condições e desconto são persistidos em um único salvamento.
    """
    m = carregar_manutencao(db, manutencao_id)
    if not m or not m.orcamentos:
        raise HTTPException(404)
    form = dict(await request.form())
    o = _orcamento_atual(m) or sorted(m.orcamentos, key=lambda x: x.versao)[-1]

    valor_manutencao = max(moeda_num(form.get("valor_manutencao")), 0)
    forma = (form.get("forma_pagamento_orcamento") or "").strip()
    try:
        prazo = int(form.get("prazo_dias_uteis") or 0)
    except (TypeError, ValueError):
        prazo = 0
    desconto = max(moeda_num(form.get("desconto")), 0)

    erros = []
    if valor_manutencao <= 0:
        erros.append("Informe o valor da manutenção.")
    if forma not in ("À vista", "50% de sinal + 50% na entrega"):
        erros.append("Selecione a forma de pagamento.")
    if prazo <= 0:
        erros.append("Informe o prazo em dias úteis.")

    if erros:
        return RedirectResponse(
            f"/organiza/manutencoes/{manutencao_id}?erro_etapa2={quote_plus(' '.join(erros))}#etapa-2",
            status_code=303,
        )

    o.valor_manutencao = valor_manutencao
    o.forma_pagamento_orcamento = forma
    o.prazo_dias_uteis = prazo
    subtotal = valor_manutencao + sum(float(i.preco_venda or 0) * int(i.quantidade or 0) for i in o.itens)
    o.desconto = min(desconto, subtotal)
    o.desconto_somente_com_opcionais = 1 if form.get("desconto_somente_com_opcionais") else 0
    db.commit()
    return RedirectResponse(
        f"/organiza/manutencoes/{manutencao_id}?salvo_etapa2=1#etapa-2",
        status_code=303,
    )


@app.post("/organiza/manutencoes/{manutencao_id}/etapa-2/avancar")
async def manutencao_etapa2_avancar(
    manutencao_id: int,
    request: Request,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    m = carregar_manutencao(db, manutencao_id)
    if not m or not m.orcamentos:
        raise HTTPException(404)
    o = _orcamento_atual(m)
    erros = []
    if not o or float(o.valor_manutencao or 0) <= 0:
        erros.append("Informe e salve o valor da manutenção.")
    if not o or not o.forma_pagamento_orcamento:
        erros.append("Informe e salve a forma de pagamento.")
    if not o or not o.prazo_dias_uteis:
        erros.append("Informe e salve o prazo.")
    if erros:
        return RedirectResponse(
            f"/organiza/manutencoes/{manutencao_id}?erro_etapa2={quote_plus(' '.join(erros))}#etapa-2",
            status_code=303,
        )
    m.status = "Aguardando aprovação"
    db.commit()
    return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}#etapa-3", status_code=303)


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


def registrar_aprovacao_orcamento(o: Orcamento, modalidade: str, origem: str) -> None:
    """Grava exatamente o que foi autorizado para a execução técnica."""
    aprovar_tudo = modalidade == "todos"
    for item in o.itens:
        item.aprovado = 1 if (not item.opcional or aprovar_tudo) else 0

    # O campo status possui limite de 40 caracteres no PostgreSQL.
    # A origem da aprovação não deve ser concatenada aqui para evitar erro 500.
    o.status = "Aprovado: todos" if aprovar_tudo else "Aprovado: obrigatórios"
    o.aprovado_em = datetime.now()
    o.manutencao.status = "Aprovado"


@app.post("/organiza/manutencoes/{manutencao_id}/aprovar-manual")
@app.post("/organiza/manutencoes/{manutencao_id}/corrigir-aprovacao")
async def aprovar_manual(
    manutencao_id: int,
    request: Request,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    m = carregar_manutencao(db, manutencao_id)
    if not m or not m.orcamentos:
        raise HTTPException(404, "Orçamento não encontrado.")
    o = sorted(m.orcamentos, key=lambda x: x.versao)[-1]
    form = dict(await request.form())
    modalidade = form.get("modalidade", "obrigatorios")
    if modalidade not in {"obrigatorios", "todos"}:
        modalidade = "obrigatorios"

    origem = "correção administrativa" if o.aprovado_em else "aprovação manual"
    registrar_aprovacao_orcamento(o, modalidade, origem)
    db.commit()
    return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}#etapa-3", status_code=303)


@app.post("/organiza/manutencoes/{manutencao_id}/pagamento")
async def pagamento_registrar(manutencao_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    m = carregar_manutencao(db, manutencao_id); form = dict(await request.form()); o = sorted(m.orcamentos, key=lambda x: x.versao)[-1]
    valor = moeda_num(form.get("valor"))
    forma = (form.get("forma") or "PIX").strip()
    banco = forma
    nome_comprovante = (form.get("observacao") or "").strip()
    observacao = _obs_pagamento_padrao(m.equipamento, m.cliente, nome_comprovante)
    total, recebido, saldo = _saldo_manutencao(m)
    if valor > saldo + 0.009:
        return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}?erro_fluxo=pagamento_excedente", status_code=303)
    if valor > 0:
        db.add(Pagamento(
            orcamento_id=o.id,
            data=data_form(form.get("data") or "") or date.today(),
            valor=valor,
            forma=forma,
            banco=banco,
            observacao=observacao or None,
        ))
        m.status = "Confirmação pendente"; db.commit()
    return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}", status_code=303)



@app.post("/organiza/manutencoes/{manutencao_id}/pagamentos/{pagamento_id}/editar")
async def manutencao_pagamento_editar(
    manutencao_id: int,
    pagamento_id: int,
    request: Request,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    m = carregar_manutencao(db, manutencao_id)
    if not m:
        raise HTTPException(404)
    ids_orcamentos = [o.id for o in m.orcamentos]
    p = db.query(Pagamento).filter(
        Pagamento.id == pagamento_id,
        Pagamento.orcamento_id.in_(ids_orcamentos),
    ).first()
    if not p:
        raise HTTPException(404)

    form = dict(await request.form())
    valor = moeda_num(form.get("valor"))
    forma = (form.get("forma") or "").strip()
    banco = forma
    data_pag = data_form(form.get("data") or "")
    if valor <= 0 or not data_pag or not forma:
        return RedirectResponse(
            f"/organiza/manutencoes/{manutencao_id}?erro_fluxo=edicao_pagamento",
            status_code=303,
        )

    o = db.query(Orcamento).filter(Orcamento.id == p.orcamento_id).first()
    total = totais_orcamento(o)["aprovado"] if o else 0
    outros = sum(float(item.valor or 0) for item in db.query(Pagamento).filter(
        Pagamento.orcamento_id == p.orcamento_id, Pagamento.id != pagamento_id
    ).all())
    if valor > round(total - outros, 2) + 0.009:
        return RedirectResponse(
            f"/organiza/manutencoes/{manutencao_id}?erro_fluxo=pagamento_excedente", status_code=303
        )

    p.valor = round(valor, 2)
    p.data = data_pag
    p.forma = forma
    p.banco = banco
    nome_comprovante = (form.get("observacao") or "").strip()
    prefixo = _obs_pagamento_padrao(m.equipamento, m.cliente)
    p.observacao = (nome_comprovante if nome_comprovante.startswith(prefixo) else _obs_pagamento_padrao(m.equipamento, m.cliente, nome_comprovante)) or None
    db.commit()
    return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}#etapa-4", status_code=303)



@app.post("/organiza/manutencoes/{manutencao_id}/etapa-4/salvar")
async def manutencao_etapa4_salvar(
    manutencao_id: int,
    request: Request,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    """Salva pagamento (quando informado) e prazo prometido em um único formulário."""
    m = carregar_manutencao(db, manutencao_id)
    if not m:
        raise HTTPException(404)
    o = _orcamento_atual(m)
    if not o:
        raise HTTPException(400, "Orçamento não encontrado.")
    form = dict(await request.form())

    prazo_texto = (form.get("prazo") or "").strip()
    try:
        prazo_data = datetime.strptime(prazo_texto, "%Y-%m-%d").date() if prazo_texto else None
    except ValueError:
        prazo_data = None

    valor_texto = (form.get("valor") or "").strip()
    valor = moeda_num(valor_texto) if valor_texto else 0
    recebido_atual = sum(float(p.valor or 0) for p in o.pagamentos)
    totais = totais_orcamento(o)
    total_aprovado = float(totais.get("aprovado", 0) or 0)
    manutencao_sem_cobranca = total_aprovado <= 0.009
    erros = []
    if not prazo_data:
        erros.append("Informe uma data válida para o prazo prometido.")
    if not manutencao_sem_cobranca and recebido_atual <= 0 and valor <= 0:
        erros.append("Registre ao menos um pagamento antes de avançar.")
    if valor < 0:
        erros.append("O valor do pagamento é inválido.")

    falta = float(totais.get("falta", 0) or 0)
    if valor > falta + 0.01:
        erros.append("O pagamento não pode ser maior que o valor que falta receber.")

    if erros:
        return RedirectResponse(
            f"/organiza/manutencoes/{manutencao_id}?erro_etapa4={quote_plus(' '.join(erros))}#etapa-4",
            status_code=303,
        )

    if valor > 0:
        data_pag = data_form(form.get("data") or "") or date.today()
        banco = (form.get("forma") or "").strip() or None
        nome_comprovante = (form.get("observacao") or "").strip()
        prefixo = _obs_pagamento_padrao(m.equipamento, m.cliente)
        observacao = (nome_comprovante if nome_comprovante.startswith(prefixo)
                      else _obs_pagamento_padrao(m.equipamento, m.cliente, nome_comprovante)) or None
        db.add(Pagamento(
            orcamento_id=o.id,
            data=data_pag,
            valor=round(valor, 2),
            forma=banco,
            banco=banco,
            observacao=observacao,
        ))
    elif manutencao_sem_cobranca:
        # Registra a conclusão financeira de R$ 0,00 apenas no Organiza.
        # O IntegracaoConect correspondente já nasce ignorado, portanto nunca
        # será enviado para a Central Financeira/Connect.
        marcador = "SEM COBRANÇA - R$ 0,00"
        pagamento_zero = next((p for p in o.pagamentos if abs(float(p.valor or 0)) < 0.009 and marcador in (p.observacao or "")), None)
        if not pagamento_zero:
            pagamento_zero = Pagamento(
                orcamento_id=o.id,
                data=data_form(form.get("data") or "") or date.today(),
                valor=0.0,
                forma=None,
                banco=None,
                observacao=f"{_obs_pagamento_padrao(m.equipamento, m.cliente)} - {marcador}",
            )
            db.add(pagamento_zero)
            db.flush()
            db.add(IntegracaoConect(
                origem="manutencao",
                registro_id=pagamento_zero.id,
                id_externo=f"ORGANIZA-MANUTENCAO-PAG-{pagamento_zero.id}",
                ignorado=1,
                resposta="Manutenção sem cobrança; pagamento zero não deve ser enviado ao Connect.",
            ))

    m.prazo = prazo_data.strftime("%d/%m/%Y")
    db.commit()
    return RedirectResponse(
        f"/organiza/manutencoes/{manutencao_id}?salvo_etapa4=1#etapa-4",
        status_code=303,
    )


@app.post("/organiza/manutencoes/{manutencao_id}/etapa-4/avancar")
def manutencao_etapa4_avancar(
    manutencao_id: int,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    m = carregar_manutencao(db, manutencao_id)
    if not m:
        raise HTTPException(404)
    o = _orcamento_atual(m)
    recebido = sum(float(p.valor or 0) for p in o.pagamentos) if o else 0
    totais = totais_orcamento(o) if o else {}
    total_aprovado = float(totais.get("aprovado", 0) or 0)
    manutencao_sem_cobranca = total_aprovado <= 0.009
    pagamento_zero_registrado = bool(o and any(
        abs(float(p.valor or 0)) < 0.009 and "SEM COBRANÇA" in (p.observacao or "")
        for p in o.pagamentos
    ))
    erros = []
    if not manutencao_sem_cobranca and recebido <= 0:
        erros.append("Registre o pagamento.")
    if manutencao_sem_cobranca and not pagamento_zero_registrado:
        erros.append("Confirme o pagamento R$ 0,00 para registrar a manutenção sem cobrança.")
    if not m.prazo:
        erros.append("Informe o prazo prometido.")
    if not m.confirmacao_prazo_em:
        erros.append("Envie a confirmação ao cliente pelo WhatsApp.")
    if erros:
        return RedirectResponse(
            f"/organiza/manutencoes/{manutencao_id}?erro_etapa4={quote_plus(' '.join(erros))}#etapa-4",
            status_code=303,
        )
    m.status = "Em manutenção"
    db.commit()
    return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}#etapa-5", status_code=303)


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
    db.commit()
    mensagem = (
        f"Olá, {m.cliente.nome}!\n\n"
        "✅ Pagamento e prazo confirmados.\n\n"
        f"Equipamento: {descricao_equipamento(m.equipamento)}\n"
        f"Código técnico: {codigo_tecnico(m.equipamento)}\n"
        f"Prazo previsto: {m.prazo}\n\n"
        "Seu pagamento e o prazo foram registrados. Assim que avançarmos o serviço para execução, iniciaremos a manutenção.\n\nKaraokê RJ"
    )
    url = ComunicacaoService.registrar_e_url(db, HistoricoComunicacao, m, usuario, "PRAZO", mensagem)
    return RedirectResponse(url, status_code=303)


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
    url = ComunicacaoService.registrar_e_url(db, HistoricoComunicacao, m, usuario, "COMPRA", mensagem)
    return RedirectResponse(url, status_code=303)


@app.get("/organiza/manutencoes/{manutencao_id}/concluir-whatsapp")
def concluir_servico_whatsapp(manutencao_id: int, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    """Conclui a Etapa 5 ou reenvia a comunicação sem bloquear o fluxo.

    A rota é idempotente: depois que o serviço já avançou, pode ser usada
    novamente para reenviar a garantia e o link de retirada.
    """
    m = carregar_manutencao(db, manutencao_id)
    if not m:
        raise HTTPException(404)

    etapa = etapa_manutencao(m)
    if etapa < 5 or m.servico_pausado_em or not (m.diagnostico or "").strip():
        return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}?erro_fluxo=conclusao", status_code=303)

    o = _orcamento_atual(m)
    if not o:
        raise HTTPException(400, "Orçamento não encontrado.")

    # Avança somente na primeira conclusão. Em reenvios, preserva datas e etapa.
    if etapa == 5:
        agora = datetime.now()
        if not m.pronto_em:
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
    url = ComunicacaoService.registrar_e_url(db, HistoricoComunicacao, m, usuario, "PRONTO", mensagem)
    return RedirectResponse(url, status_code=303)


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
    if not os.path.exists(logo_path):
        logo_path = os.path.join(os.path.dirname(__file__), "static", "img", "karaoke-rj-garantia.jpeg")
    logo = Image(logo_path, width=30*mm, height=22*mm) if os.path.exists(logo_path) else Spacer(30*mm, 22*mm)
    empresa = Paragraph(
        "<b>KARAOKE &amp; GAMES RJ</b><br/>CNPJ: 35.458.112/0001-75 · IM: 1213508-4<br/>"
        "Rua João Romariz, 313 - Ramos - Rio de Janeiro/RJ - CEP: 21031-700<br/>"
        "WhatsApp: (21) 99507-9690 / (21) 99650-4516<br/>www.karaokerj.com.br · contato@karaokerj.com.br",
        ParagraphStyle("CabecalhoGarantiaServico", parent=corpo, fontSize=8, leading=10, spaceAfter=0),
    )
    header = Table([[logo, empresa]], colWidths=[35*mm, 139*mm])
    header.setStyle(TableStyle([
        ("VALIGN",(0,0),(-1,-1),"MIDDLE"), ("LINEBELOW",(0,0),(-1,-1),0.8,colors.HexColor("#555555")),
        ("LEFTPADDING",(0,0),(-1,-1),0), ("RIGHTPADDING",(0,0),(-1,-1),0), ("BOTTOMPADDING",(0,0),(-1,-1),5),
    ]))
    elementos += [header, Spacer(1, 7), Paragraph("CERTIFICADO DE GARANTIA DO SERVIÇO", titulo), Spacer(1, 4*mm)]
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
    agendamento_informado = (form.get("entrega_prevista_em") or "").strip()
    agendamento = datetime_form(agendamento_informado) if agendamento_informado else m.entrega_prevista_em

    # Depois que o equipamento entrou, o técnico pode corrigir diagnóstico e
    # observações sem ser bloqueado por uma previsão antiga, vazia ou vencida.
    alterou_agendamento = agendamento != m.entrega_prevista_em or tipo_atendimento != (m.tipo_atendimento or "loja")
    if tipo_atendimento not in ("loja", "online"):
        return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}?erro_agendamento=1", status_code=303)
    if alterou_agendamento and not m.recebido_em:
        if not horario_atendimento_valido(tipo_atendimento, agendamento) or horario_atendimento_ocupado(db, agendamento, m.id):
            return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}?erro_agendamento=1", status_code=303)

    m.tipo_atendimento = tipo_atendimento
    m.entrega_prevista_em = agendamento
    db.commit()
    return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}#etapa-1", status_code=303)

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
    form = dict(await request.form())
    valor = moeda_num(form.get("valor"))
    o = db.query(Orcamento).filter(Orcamento.id == p.orcamento_id).first()
    total = totais_orcamento(o)["aprovado"] if o else 0
    outros = sum(float(item.valor or 0) for item in db.query(Pagamento).filter(
        Pagamento.orcamento_id == p.orcamento_id, Pagamento.id != pagamento_id
    ).all())
    if valor <= 0 or valor > round(total - outros, 2) + 0.009:
        return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}?erro_fluxo=pagamento_excedente", status_code=303)
    p.data = data_form(form.get("data") or "") or p.data
    p.valor = round(valor, 2)
    p.forma = (form.get("forma") or "PIX").strip()
    p.banco = p.forma
    p.observacao = (form.get("observacao") or "").strip() or None
    db.commit()
    return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}", status_code=303)

@app.post("/organiza/manutencoes/{manutencao_id}/pagamento/{pagamento_id}/excluir")
def pagamento_excluir(manutencao_id: int, pagamento_id: int, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    p = db.query(Pagamento).filter(Pagamento.id == pagamento_id).first()
    if not p:
        raise HTTPException(404)
    integ = _registro_integracao(db, "manutencao", p.id)
    if integ and integ.enviado_em:
        return RedirectResponse(
            f"/organiza/manutencoes/{manutencao_id}?erro_fluxo=pagamento_enviado_connect", status_code=303
        )
    if integ:
        db.delete(integ)
    db.delete(p)
    db.commit()
    return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}", status_code=303)

@app.post("/organiza/manutencoes/{manutencao_id}/encerrar")
def manutencao_encerrar(manutencao_id: int, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    m=db.query(Manutencao).filter(Manutencao.id==manutencao_id).first()
    if not m or not m.retirada_em: raise HTTPException(400, "Agende a retirada antes de encerrar")
    m.entregue_em=datetime.now(); m.status="Encerrada"; db.commit()
    return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}", status_code=303)


def _manutencoes_operacao(db: Session):
    return (
        db.query(Manutencao)
        .options(
            selectinload(Manutencao.cliente),
            selectinload(Manutencao.equipamento),
            selectinload(Manutencao.orcamentos).selectinload(Orcamento.itens),
            selectinload(Manutencao.orcamentos).selectinload(Orcamento.pagamentos),
        )
        .filter(Manutencao.entregue_em.is_(None), ~Manutencao.status.in_(("Encerrada", "Cancelada")))
        .order_by(Manutencao.criado_em.asc(), Manutencao.id.asc())
        .all()
    )


def _saldo_manutencao(m: Manutencao):
    o = _orcamento_atual(m)
    if not o:
        return 0.0, 0.0, 0.0
    totais = totais_orcamento(o)
    total = float(totais.get("aprovado", 0) or 0)
    recebido = sum(float(p.valor or 0) for p in o.pagamentos)
    return total, recebido, max(total - recebido, 0)


def _agrupar_por_cliente(manutencoes):
    grupos = {}
    for m in manutencoes:
        grupo = grupos.setdefault(m.cliente_id, {
            "cliente": m.cliente,
            "manutencoes": [],
            "total": 0.0,
            "recebido": 0.0,
            "saldo": 0.0,
        })
        grupo["manutencoes"].append(m)
        total, recebido, saldo = _saldo_manutencao(m)
        grupo["total"] += total
        grupo["recebido"] += recebido
        grupo["saldo"] += saldo
    return sorted(grupos.values(), key=lambda g: (g["cliente"].nome or "").lower())


templates.env.globals["_saldo_manutencao"] = _saldo_manutencao


def _orcamento_pronto_para_comunicar(m):
    """Orçamento completo, salvo e ainda não enviado ao cliente."""
    o = _orcamento_atual(m)
    if not o:
        return False
    tem_valor = float(o.valor_manutencao or 0) > 0 or any(float(i.preco_venda or 0) > 0 for i in o.itens)
    tem_condicoes = bool(o.forma_pagamento_orcamento) and o.prazo_dias_uteis is not None
    nao_enviado = o.status in ("Rascunho", "Em elaboração", "Pronto", None, "")
    return etapa_manutencao(m) == 2 and bool(o.itens or float(o.valor_manutencao or 0) > 0) and tem_valor and tem_condicoes and nao_enviado


def _tipo_comunicacao(m):
    if _orcamento_pronto_para_comunicar(m):
        return "orcamento"
    if etapa_manutencao(m) == 6 and m.pronto_em:
        return "pronto"
    return None


def _status_comunicacao(m, tipo):
    o = _orcamento_atual(m)
    if tipo == "orcamento":
        return bool(o and o.status in ("Enviado", "Aguardando aprovação"))
    if tipo == "pronto":
        return bool(m.conclusao_comunicada_em)
    return False


def _grupos_comunicacao(manutencoes, incluir_comunicados=True):
    grupos = {}
    for m in manutencoes:
        tipo = _tipo_comunicacao(m)
        # Também traz os já comunicados da etapa correspondente para permitir reenvio.
        if not tipo:
            o = _orcamento_atual(m)
            if etapa_manutencao(m) == 3 and o and o.status in ("Enviado", "Aguardando aprovação"):
                tipo = "orcamento"
            elif etapa_manutencao(m) == 6 and m.pronto_em:
                tipo = "pronto"
        if not tipo:
            continue
        comunicado = _status_comunicacao(m, tipo)
        if comunicado and not incluir_comunicados:
            continue
        chave = (m.cliente_id, tipo)
        grupo = grupos.setdefault(chave, {
            "cliente": m.cliente, "tipo": tipo, "manutencoes": [],
            "comunicado": True, "ultima_comunicacao": None,
        })
        grupo["manutencoes"].append(m)
        grupo["comunicado"] = grupo["comunicado"] and comunicado
        datas = []
        o = _orcamento_atual(m)
        if tipo == "orcamento" and o and o.status in ("Enviado", "Aguardando aprovação"):
            datas.append(o.criado_em)
        if tipo == "pronto" and m.conclusao_comunicada_em:
            datas.append(m.conclusao_comunicada_em)
        for d in datas:
            if d and (grupo["ultima_comunicacao"] is None or d > grupo["ultima_comunicacao"]):
                grupo["ultima_comunicacao"] = d
    resultado = list(grupos.values())
    for grupo in resultado:
        grupo["manutencoes"].sort(key=lambda m: (prefixo_equipamento(m.equipamento.tipo), m.equipamento.numero_maquina_cliente or 999999))
    return sorted(resultado, key=lambda g: (g["comunicado"], (g["cliente"].nome or "").lower(), g["tipo"]))


def _montar_mensagem_comunicacao(cliente, selecionadas, tipo):
    if not cliente.token_ficha:
        cliente.token_ficha = secrets.token_urlsafe(24)

    linhas = [f"Olá, {cliente.nome}!"]

    if tipo == "orcamento":
        linhas += ["", "*Seus orçamentos estão prontos:*"]
        total_geral = 0.0

        for m in selecionadas:
            orcamento = _orcamento_atual(m)
            if not orcamento:
                continue

            totais = totais_orcamento(orcamento)
            total_obrigatorio = totais["obrigatorio_final"]
            total_completo = totais["geral"]
            total_geral += total_obrigatorio

            linhas += ["", f"*{rotulo_maquina(m.equipamento)}*"]

            obrigatorios = []
            if orcamento.valor_manutencao and orcamento.valor_manutencao > 0:
                obrigatorios.append("Serviço de manutenção")
            obrigatorios.extend(i.descricao for i in orcamento.itens if not i.opcional)

            if obrigatorios:
                linhas.append("*Itens obrigatórios:*")
                for descricao in dict.fromkeys(obrigatorios):
                    linhas.append(f"• {descricao}")

            opcionais = [i for i in orcamento.itens if i.opcional]
            if opcionais:
                linhas.append("*Opcionais:*")
                for item in opcionais:
                    valor_opcional = float(item.preco_venda or 0) * int(item.quantidade or 0)
                    quantidade = int(item.quantidade or 0)
                    complemento_quantidade = f" ({quantidade}x)" if quantidade > 1 else ""
                    linhas.append(
                        f"• {item.descricao}{complemento_quantidade} — {formatar_moeda(valor_opcional)}"
                    )

            linhas.append(f"Total obrigatório: {formatar_moeda(total_obrigatorio)}")
            if totais["desconto_condicional"] or totais["opcionais"] > 0:
                if totais["desconto_informado"] > 0:
                    if totais["desconto_condicional"]:
                        rotulo_desconto = "Desconto ao aprovar todos os opcionais"
                    else:
                        rotulo_desconto = "Desconto aplicado ao total do orçamento"
                    linhas.append(
                        f"{rotulo_desconto}: - {formatar_moeda(totais['desconto_informado'])}"
                    )
                linhas.append(
                    f"Total com opcionais e desconto: {formatar_moeda(total_completo)}"
                )

        if len(selecionadas) > 1:
            totais_validos = [
                totais_orcamento(_orcamento_atual(item))
                for item in selecionadas
                if _orcamento_atual(item)
            ]
            total_geral_completo = sum(item["geral"] for item in totais_validos)
            linhas += ["", f"*Total geral obrigatório: {formatar_moeda(total_geral)}*"]
            if any(item["desconto_condicional"] or item["opcionais"] > 0 for item in totais_validos):
                linhas.append(
                    f"*Total geral com opcionais e descontos: {formatar_moeda(total_geral_completo)}*"
                )

        linhas += ["", "Abra o link para revisar e responder cada orçamento."]
    else:
        linhas += ["", f"✅ Seus {len(selecionadas)} equipamento(s) estão prontos:"]
        for m in selecionadas:
            linhas.append(f"• {descricao_equipamento(m.equipamento)}")
        linhas += ["", "Acesse para consultar as garantias e agendar uma única retirada."]

    linhas += ["", "Acompanhe tudo aqui:", f"{PUBLIC_BASE_URL}/acompanhar/{cliente.token_ficha}", "", "Karaokê RJ"]
    return "\n".join(linhas)

def _fila_operacional_exclusiva(m):
    """Retorna uma única fila operacional para cada manutenção."""
    etapa = etapa_manutencao(m)

    if etapa == 1:
        return "atendimento"

    if etapa == 2:
        return "comunicar_orcamentos" if _orcamento_pronto_para_comunicar(m) else "orcamentos"

    if etapa == 3:
        return "aprovacoes"

    if etapa == 4:
        return "pagamentos"

    if etapa == 5:
        return "pausados" if m.servico_pausado_em else "execucao"

    if etapa == 6:
        return "prontos" if not m.conclusao_comunicada_em else "retiradas"

    return None




ETAPAS_OPERACAO_AGRUPADA = {
    "atendimento": {
        "titulo": "Aguardando atendimento ou entrega",
        "descricao": "Selecione os equipamentos entregues pelo cliente e confirme todos de uma vez.",
    },
    "aprovacoes": {
        "titulo": "Aguardando aprovação",
        "descricao": "Acompanhe e reenvie, em uma única mensagem, os orçamentos selecionados do cliente.",
    },
    "execucao": {
        "titulo": "Em execução",
        "descricao": "Somente equipamentos aprovados e liberados para execução.",
    },
    "pausados": {
        "titulo": "Aguardando item",
        "descricao": "Equipamentos em execução pausados por peça ou material.",
    },
    "retiradas": {
        "titulo": "Aguardando retirada",
        "descricao": "Selecione os equipamentos entregues ao cliente e finalize todos de uma vez.",
    },
}


@app.get("/organiza/operacao/etapa/{chave}", response_class=HTMLResponse)
def operacao_etapa_agrupada(
    chave: str,
    request: Request,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    configuracao = ETAPAS_OPERACAO_AGRUPADA.get(chave)
    if not configuracao:
        raise HTTPException(404)
    manutencoes = [
        m for m in _manutencoes_operacao(db)
        if _fila_operacional_exclusiva(m) == chave
    ]
    grupos = _agrupar_por_cliente(manutencoes)
    if chave == "aprovacoes":
        for grupo in grupos:
            grupo["comunicado"] = all(_status_comunicacao(m, "orcamento") for m in grupo["manutencoes"])
    return templates.TemplateResponse("organiza/operacao_etapa_agrupada.html", {
        "request": request,
        "usuario": usuario,
        "chave": chave,
        "titulo": configuracao["titulo"],
        "descricao": configuracao["descricao"],
        "grupos": grupos,
        "data_hoje": datetime.now().date().isoformat(),
        "sucesso": request.query_params.get("sucesso", ""),
        "erro": request.query_params.get("erro", ""),
    })


@app.post("/organiza/operacao/execucao/concluir")
async def operacao_execucao_concluir(
    request: Request,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    form = await request.form()
    try:
        ids = [int(x) for x in form.getlist("manutencao_id")]
    except ValueError:
        ids = []
    selecionadas = [m for m in _manutencoes_operacao(db) if m.id in ids and _fila_operacional_exclusiva(m) == "execucao"]
    if not selecionadas:
        return RedirectResponse("/organiza/operacao/etapa/execucao?erro=Selecione pelo menos um equipamento", status_code=303)

    agora = datetime.now()
    for m in selecionadas:
        # A conclusão pela Central apenas avança para a etapa de comunicação.
        # O cliente ainda não é marcado como comunicado aqui.
        if not m.pronto_em:
            m.pronto_em = agora
        m.status = "Pronto para retirada"
        m.conclusao_comunicada_em = None
    db.commit()
    return RedirectResponse(
        f"/organiza/operacao/etapa/execucao?sucesso={quote_plus(str(len(selecionadas)) + ' equipamento(s) enviado(s) para Comunicar equipamentos prontos')}",
        status_code=303,
    )


@app.get("/organiza/operacao/execucao/imprimir", response_class=HTMLResponse)
def operacao_execucao_imprimir(
    request: Request,
    ids: str = "",
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    manutencao_ids = {int(valor) for valor in ids.split(",") if valor.strip().isdigit()}
    manutencoes = [
        m for m in _manutencoes_operacao(db)
        if m.id in manutencao_ids and _fila_operacional_exclusiva(m) == "execucao"
    ]
    manutencoes.sort(key=lambda m: ((m.cliente.nome or "").lower(), m.id))

    fichas = []
    for m in manutencoes:
        orcamento = _orcamento_atual(m)
        itens = []
        if orcamento:
            itens = [
                item for item in orcamento.itens
                if (not item.opcional) or item.aprovado
            ]
        total, recebido, saldo = _saldo_manutencao(m)
        fichas.append({
            "manutencao": m,
            "itens": itens,
            "total": total,
            "recebido": recebido,
            "saldo": saldo,
        })

    return templates.TemplateResponse("organiza/operacao_execucao_impressao.html", {
        "request": request,
        "usuario": usuario,
        "fichas": fichas,
        "data_impressao": datetime.now(),
    })


@app.post("/organiza/operacao/receber")
async def operacao_receber_em_lote(
    request: Request,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    form = await request.form()
    try:
        ids = sorted({int(x) for x in form.getlist("manutencao_id")})
    except ValueError:
        ids = []
    selecionadas = [
        m for m in _manutencoes_operacao(db)
        if m.id in ids and _fila_operacional_exclusiva(m) == "atendimento"
    ]
    if not selecionadas:
        return RedirectResponse(
            "/organiza/operacao/etapa/atendimento?erro=Selecione pelo menos um equipamento",
            status_code=303,
        )
    data_texto = (form.get("data_recebimento") or "").strip()
    try:
        data_recebimento = datetime.strptime(data_texto, "%Y-%m-%d").date()
    except ValueError:
        return RedirectResponse(
            "/organiza/operacao/etapa/atendimento?erro=Informe uma data de recebimento válida",
            status_code=303,
        )

    horario_atual = datetime.now().time().replace(microsecond=0)
    recebido_em = datetime.combine(data_recebimento, horario_atual)
    for m in selecionadas:
        m.recebido_em = recebido_em
        m.status = "Orçamento em elaboração"
    db.commit()
    return RedirectResponse(
        f"/organiza/operacao/etapa/atendimento?sucesso={len(selecionadas)} equipamento(s) recebido(s)",
        status_code=303,
    )


@app.post("/organiza/operacao/finalizar-retiradas")
async def operacao_finalizar_retiradas(
    request: Request,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    form = await request.form()
    try:
        ids = sorted({int(x) for x in form.getlist("manutencao_id")})
    except ValueError:
        ids = []

    selecionadas = [
        m for m in _manutencoes_operacao(db)
        if m.id in ids and _fila_operacional_exclusiva(m) == "retiradas"
    ]
    if not selecionadas:
        return RedirectResponse(
            "/organiza/operacao/etapa/retiradas?erro=Selecione pelo menos um equipamento",
            status_code=303,
        )

    data_texto = (form.get("data_entrega") or "").strip()
    try:
        data_entrega = datetime.strptime(data_texto, "%Y-%m-%d").date()
    except ValueError:
        return RedirectResponse(
            "/organiza/operacao/etapa/retiradas?erro=Informe uma data de entrega válida",
            status_code=303,
        )

    horario_atual = datetime.now().time().replace(microsecond=0)
    entregue_em = datetime.combine(data_entrega, horario_atual)

    for m in selecionadas:
        m.entregue_em = entregue_em
        if not m.retirada_em:
            m.retirada_em = entregue_em
        m.status = "Encerrada"
        if m.equipamento:
            m.equipamento.status = "Ativo"

    db.commit()
    return RedirectResponse(
        f"/organiza/operacao/etapa/retiradas?sucesso={len(selecionadas)} equipamento(s) finalizado(s)",
        status_code=303,
    )


@app.get("/organiza/operacao/orcamentos", response_class=HTMLResponse)
def operacao_orcamentos(
    request: Request,
    abrir: int | None = None,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    """Fila de orçamentos editável sem entrar na manutenção."""
    manutencoes = [
        m for m in _manutencoes_operacao(db)
        if _fila_operacional_exclusiva(m) == "orcamentos"
    ]
    itens_catalogo = (
        db.query(Item)
        .filter(Item.ativo == 1)
        .order_by(Item.categoria.asc(), Item.nome.asc())
        .all()
    )
    grupos_por_cliente = {}
    for manutencao in manutencoes:
        grupo = grupos_por_cliente.setdefault(
            manutencao.cliente_id,
            {"cliente": manutencao.cliente, "manutencoes": []},
        )
        grupo["manutencoes"].append(manutencao)

    return templates.TemplateResponse("organiza/operacao_orcamentos.html", {
        "request": request,
        "usuario": usuario,
        "manutencoes": manutencoes,
        "grupos": list(grupos_por_cliente.values()),
        "itens_catalogo": itens_catalogo,
        "abrir": abrir,
        "sucesso": request.query_params.get("sucesso", ""),
        "erro": request.query_params.get("erro", ""),
    })


@app.post("/organiza/operacao/orcamentos/salvar-lote")
async def operacao_orcamentos_salvar_lote(
    request: Request,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    """Salva diagnósticos e orçamentos selecionados em uma única ação."""
    form = await request.form()
    try:
        ids = sorted({int(valor) for valor in form.getlist("manutencao_id")})
    except (TypeError, ValueError):
        ids = []

    if not ids:
        return RedirectResponse(
            "/organiza/operacao/orcamentos?erro=Selecione pelo menos um equipamento",
            status_code=303,
        )

    manutencoes = [
        m for m in _manutencoes_operacao(db)
        if m.id in ids and _fila_operacional_exclusiva(m) == "orcamentos"
    ]
    if len(manutencoes) != len(ids):
        return RedirectResponse(
            "/organiza/operacao/orcamentos?erro=Um ou mais equipamentos não estão aguardando orçamento",
            status_code=303,
        )

    erros = []
    preparados = []
    for m in manutencoes:
        diagnostico = (form.get(f"diagnostico_{m.id}") or "").strip()
        valor_manutencao = max(moeda_num(form.get(f"valor_manutencao_{m.id}")), 0)
        forma = (form.get(f"forma_pagamento_orcamento_{m.id}") or "").strip()
        try:
            prazo = int(form.get(f"prazo_dias_uteis_{m.id}") or 0)
        except (TypeError, ValueError):
            prazo = 0

        if not diagnostico:
            erros.append(f"{m.equipamento.codigo}: informe o diagnóstico")
        if valor_manutencao <= 0:
            erros.append(f"{m.equipamento.codigo}: informe o valor da manutenção")
        if forma not in ("À vista", "50% de sinal + 50% na entrega"):
            erros.append(f"{m.equipamento.codigo}: informe a forma de pagamento")
        if prazo <= 0:
            erros.append(f"{m.equipamento.codigo}: informe o prazo em dias úteis")

        item_ids = form.getlist(f"item_id_{m.id}")
        quantidades = form.getlist(f"quantidade_{m.id}")
        opcionais = set(form.getlist(f"opcional_{m.id}"))
        novos_itens = []
        for indice, item_id_texto in enumerate(item_ids):
            if not item_id_texto:
                continue
            try:
                item_id = int(item_id_texto)
                quantidade = max(int(quantidades[indice] if indice < len(quantidades) else 1), 1)
            except (TypeError, ValueError):
                continue
            item = db.query(Item).filter(Item.id == item_id, Item.ativo == 1).first()
            if item:
                novos_itens.append((item, quantidade, str(indice) in opcionais))

        preparados.append({
            "manutencao": m, "diagnostico": diagnostico,
            "valor": valor_manutencao, "forma": forma, "prazo": prazo,
            "desconto": max(moeda_num(form.get(f"desconto_{m.id}")), 0),
            "desconto_opcionais": bool(form.get(f"desconto_somente_com_opcionais_{m.id}")),
            "itens": novos_itens,
        })

    if erros:
        return RedirectResponse(
            f"/organiza/operacao/orcamentos?erro={quote_plus(' | '.join(erros[:5]))}",
            status_code=303,
        )

    for dados in preparados:
        m = dados["manutencao"]
        o = _orcamento_atual(m)
        if not o:
            o = Orcamento(manutencao_id=m.id, versao=1, token=secrets.token_urlsafe(24), status="Rascunho")
            db.add(o); db.flush()
        for antigo in list(o.itens):
            db.delete(antigo)
        db.flush()
        total_itens = 0
        for item, quantidade, opcional in dados["itens"]:
            total_itens += float(item.preco_venda or 0) * quantidade
            db.add(OrcamentoItem(
                orcamento_id=o.id, item_id=item.id, descricao=item.nome,
                quantidade=quantidade, preco_custo=float(item.preco_custo or 0),
                preco_venda=float(item.preco_venda or 0),
                opcional=1 if opcional else 0, aprovado=0 if opcional else 1,
            ))
        m.diagnostico = dados["diagnostico"]
        o.valor_manutencao = dados["valor"]
        o.forma_pagamento_orcamento = dados["forma"]
        o.prazo_dias_uteis = dados["prazo"]
        o.desconto = min(dados["desconto"], dados["valor"] + total_itens)
        o.desconto_somente_com_opcionais = 1 if dados["desconto_opcionais"] else 0
        o.status = "Pronto"
        m.status = "Orçamento pronto"

    db.commit()
    return RedirectResponse(
        f"/organiza/operacao/orcamentos?sucesso={len(preparados)} orçamento(s) salvo(s) e enviado(s) para a próxima etapa",
        status_code=303,
    )


@app.post("/organiza/operacao/orcamentos/{manutencao_id}/salvar")
async def operacao_orcamento_salvar(
    manutencao_id: int,
    request: Request,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    """Salva o orçamento completo em uma única ação e move para comunicação."""
    m = carregar_manutencao(db, manutencao_id)
    if not m or etapa_manutencao(m) != 2:
        return RedirectResponse(
            "/organiza/operacao/orcamentos?erro=Este equipamento não está aguardando orçamento",
            status_code=303,
        )

    o = _orcamento_atual(m)
    if not o:
        o = Orcamento(
            manutencao_id=m.id,
            versao=1,
            token=secrets.token_urlsafe(24),
            status="Rascunho",
        )
        db.add(o)
        db.flush()

    form = await request.form()
    valor_manutencao = max(moeda_num(form.get("valor_manutencao")), 0)
    forma = (form.get("forma_pagamento_orcamento") or "").strip()
    if forma not in ("À vista", "50% de sinal + 50% na entrega"):
        forma = ""
    try:
        prazo = int(form.get("prazo_dias_uteis") or 0)
    except (TypeError, ValueError):
        prazo = 0

    item_ids = form.getlist("item_id")
    quantidades = form.getlist("quantidade")
    opcionais = set(form.getlist("opcional"))

    novos_itens = []
    for indice, item_id_texto in enumerate(item_ids):
        if not item_id_texto:
            continue
        try:
            item_id = int(item_id_texto)
            quantidade = max(int(quantidades[indice] if indice < len(quantidades) else 1), 1)
        except (TypeError, ValueError):
            continue
        item = db.query(Item).filter(Item.id == item_id, Item.ativo == 1).first()
        if not item:
            continue
        opcional = str(indice) in opcionais
        novos_itens.append(OrcamentoItem(
            orcamento_id=o.id,
            item_id=item.id,
            descricao=item.nome,
            quantidade=quantidade,
            preco_custo=float(item.preco_custo or 0),
            preco_venda=float(item.preco_venda or 0),
            opcional=1 if opcional else 0,
            aprovado=0 if opcional else 1,
        ))

    if valor_manutencao <= 0:
        return RedirectResponse(
            f"/organiza/operacao/orcamentos?abrir={m.id}&erro=Informe o valor da manutenção",
            status_code=303,
        )
    if not forma:
        return RedirectResponse(
            f"/organiza/operacao/orcamentos?abrir={m.id}&erro=Informe a forma de pagamento",
            status_code=303,
        )
    if prazo <= 0:
        return RedirectResponse(
            f"/organiza/operacao/orcamentos?abrir={m.id}&erro=Informe o prazo em dias úteis",
            status_code=303,
        )

    # Substitui o rascunho em uma única gravação para evitar itens duplicados.
    for item_antigo in list(o.itens):
        db.delete(item_antigo)
    db.flush()
    for novo in novos_itens:
        db.add(novo)

    o.valor_manutencao = valor_manutencao
    o.forma_pagamento_orcamento = forma
    o.prazo_dias_uteis = prazo
    o.desconto = min(max(moeda_num(form.get("desconto")), 0), valor_manutencao + sum(i.preco_venda * i.quantidade for i in novos_itens))
    o.desconto_somente_com_opcionais = 1 if form.get("desconto_somente_com_opcionais") else 0
    o.status = "Pronto"
    m.status = "Orçamento pronto"
    db.commit()

    return RedirectResponse(
        "/organiza/operacao/orcamentos?sucesso=Orçamento salvo e enviado para a fila de comunicação",
        status_code=303,
    )


@app.get("/organiza/operacao", response_class=HTMLResponse)
def operacao_painel(request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    contexto = _contexto_operacao(db)
    contexto.update({"request": request, "usuario": usuario, "pagina_inicial": False})
    return templates.TemplateResponse("organiza/operacao.html", contexto)


@app.get("/organiza/operacao/comunicacoes", response_class=HTMLResponse)
def operacao_comunicacoes(request: Request, tipo: str = "todos", usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    manutencoes = _manutencoes_operacao(db)
    grupos = _grupos_comunicacao(manutencoes, incluir_comunicados=True)
    if tipo in ("orcamento", "pronto"):
        grupos = [g for g in grupos if g["tipo"] == tipo]
    return templates.TemplateResponse("organiza/operacao_comunicacoes.html", {
        "request": request, "usuario": usuario, "grupos": grupos, "tipo_filtro": tipo
    })


@app.post("/organiza/operacao/comunicacoes/preparar")
async def operacao_comunicacoes_preparar(request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    form = await request.form()
    try:
        ids = sorted({int(x) for x in form.getlist("manutencao_id")})
    except ValueError:
        ids = []
    tipo = (form.get("tipo") or "").strip()
    selecionadas = [m for m in _manutencoes_operacao(db) if m.id in ids]
    if not selecionadas or tipo not in ("orcamento", "pronto"):
        return JSONResponse({"ok": False, "erro": "Seleção inválida."}, status_code=400)
    if len({m.cliente_id for m in selecionadas}) != 1:
        return JSONResponse({"ok": False, "erro": "Selecione equipamentos de um único cliente."}, status_code=400)
    cliente = selecionadas[0].cliente
    if tipo == "orcamento":
        for m in selecionadas:
            o = _orcamento_atual(m)
            if o:
                o.status = "Enviado"
                m.status = "Aguardando aprovação"
    else:
        agora = datetime.now()
        for m in selecionadas:
            m.conclusao_comunicada_em = m.conclusao_comunicada_em or agora
    mensagem = _montar_mensagem_comunicacao(cliente, selecionadas, tipo)
    db.commit()
    for manutencao in selecionadas:
        ComunicacaoService.registrar(db, HistoricoComunicacao, manutencao, usuario, tipo.upper(), mensagem=mensagem)
    return JSONResponse({
        "ok": True,
        "whatsapp_url": ComunicacaoService.url_whatsapp(cliente, mensagem),
        "cliente": cliente.nome,
        "quantidade": len(selecionadas),
        "tipo": tipo,
    })


@app.get("/organiza/operacao/comunicacoes/enviar")
def operacao_comunicacoes_enviar(ids: str, tipo: str = "", usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    """Compatibilidade com links antigos."""
    try:
        manutencao_ids = sorted({int(x) for x in ids.split(",") if x.strip()})
    except ValueError:
        raise HTTPException(400, "Seleção inválida.")
    selecionadas = [m for m in _manutencoes_operacao(db) if m.id in manutencao_ids]
    if not selecionadas:
        return RedirectResponse("/organiza/operacao/comunicacoes", status_code=303)
    if not tipo:
        tipo = _tipo_comunicacao(selecionadas[0]) or "orcamento"
    cliente = selecionadas[0].cliente
    if tipo == "orcamento":
        for m in selecionadas:
            o = _orcamento_atual(m)
            if o:
                o.status = "Enviado"
                m.status = "Aguardando aprovação"
    else:
        for m in selecionadas:
            m.conclusao_comunicada_em = m.conclusao_comunicada_em or datetime.now()
    mensagem = _montar_mensagem_comunicacao(cliente, selecionadas, tipo)
    db.commit()
    for manutencao in selecionadas:
        ComunicacaoService.registrar(db, HistoricoComunicacao, manutencao, usuario, tipo.upper(), mensagem=mensagem)
    return RedirectResponse(ComunicacaoService.url_whatsapp(cliente, mensagem), status_code=303)



def _mensagem_central_generica(cliente, manutencoes, tipo):
    nome = (cliente.nome or "cliente").strip().split()[0]
    itens = "\n".join(f"• {rotulo_maquina(m.equipamento)} - {(m.equipamento.modelo or m.equipamento.tipo or 'equipamento')}" for m in manutencoes)
    cab = f"Olá, {nome}! Aqui é da Karaoke RJ."
    mensagens = {
        "lembrete": f"{cab}\n\nEstamos lembrando do atendimento/entrega dos equipamentos abaixo:\n{itens}\n\nCaso precise alterar a data, fale conosco.",
        "cobranca": f"{cab}\n\nSegue um lembrete sobre o pagamento da manutenção dos equipamentos abaixo:\n{itens}\n\nApós o pagamento, envie o comprovante por aqui.",
        "previsao": f"{cab}\n\nAtualização da manutenção dos equipamentos abaixo:\n{itens}\n\nEm caso de dúvida sobre o prazo, fale conosco.",
    }
    return mensagens.get(tipo, f"{cab}\n\nAtualização sobre:\n{itens}")


@app.get("/organiza/central/comunicar")
def central_comunicar(ids: str, tipo: str, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    try:
        manutencao_ids = sorted({int(x) for x in ids.split(",") if x.strip()})
    except ValueError:
        raise HTTPException(400, "Seleção inválida.")
    selecionadas = [m for m in _manutencoes_operacao(db) if m.id in manutencao_ids]
    if not selecionadas:
        raise HTTPException(404, "Nenhum chamado encontrado.")
    if len({m.cliente_id for m in selecionadas}) != 1:
        raise HTTPException(400, "Selecione equipamentos do mesmo cliente.")
    if tipo in ("orcamento", "pronto"):
        return operacao_comunicacoes_enviar(ids=ids, tipo=tipo, usuario=usuario, db=db)
    if tipo not in ("lembrete", "cobranca", "previsao"):
        raise HTTPException(400, "Tipo de comunicação inválido.")
    cliente = selecionadas[0].cliente
    mensagem = _mensagem_central_generica(cliente, selecionadas, tipo)
    for manutencao in selecionadas:
        ComunicacaoService.registrar(db, HistoricoComunicacao, manutencao, usuario, tipo.upper(), mensagem=mensagem)
    return RedirectResponse(ComunicacaoService.url_whatsapp(cliente, mensagem), status_code=303)

@app.get("/acompanhar/{token}", response_class=HTMLResponse)
def acompanhamento_cliente(token: str, request: Request, db: Session = Depends(get_db)):
    cliente = db.query(Cliente).filter(Cliente.token_ficha == token).first()
    if not cliente:
        raise HTTPException(404)
    manutencoes = (
        db.query(Manutencao)
        .options(
            selectinload(Manutencao.equipamento),
            selectinload(Manutencao.orcamentos).selectinload(Orcamento.itens),
        )
        .filter(Manutencao.cliente_id == cliente.id)
        .order_by(Manutencao.criado_em.desc())
        .all()
    )
    registros = []
    for m in manutencoes:
        o = _orcamento_atual(m)
        if m.entregue_em or (o and o.status in ("Enviado", "Aguardando aprovação", "Aprovado", "Aprovado parcialmente", "Aprovado manualmente")) or m.pronto_em:
            registros.append({
                "m": m,
                "orcamento": o,
                "totais": totais_orcamento(o) if o else None,
                "etapa": etapa_manutencao(m),
                "meta": info_etapa_manutencao(m),
                "retirada_agendada": bool(m.retirada_em),
                "retirada_em": m.retirada_em,
            })
    return templates.TemplateResponse("organiza/acompanhamento_cliente.html", {
        "request": request,
        "cliente": cliente,
        "registros": registros,
        "tem_prontos": any(bool(r["m"].pronto_em) for r in registros),
        "usuario": None,
    })



@app.post("/organiza/operacao/aprovacoes/manual")
async def operacao_aprovacao_manual(
    request: Request,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    """Registra no painel a mesma decisão disponível ao cliente no portal público."""
    form = await request.form()
    try:
        ids = sorted({int(x) for x in form.getlist("manutencao_id")})
    except (TypeError, ValueError):
        ids = []

    acao = (form.get("acao") or "").strip().lower()
    if acao not in {"obrigatorios", "todos", "cancelar"}:
        return JSONResponse(
            {"ok": False, "erro": "Escolha uma ação válida."},
            status_code=400,
        )
    if not ids:
        return JSONResponse(
            {"ok": False, "erro": "Selecione pelo menos um equipamento."},
            status_code=400,
        )

    manutencoes = [
        m for m in _manutencoes_operacao(db)
        if m.id in ids and _fila_operacional_exclusiva(m) == "aprovacoes"
    ]
    if len(manutencoes) != len(ids):
        return JSONResponse(
            {"ok": False, "erro": "Um ou mais equipamentos não estão aguardando aprovação."},
            status_code=400,
        )
    if len({m.cliente_id for m in manutencoes}) != 1:
        return JSONResponse(
            {"ok": False, "erro": "A aprovação manual deve ser feita para um cliente por vez."},
            status_code=400,
        )

    for manutencao in manutencoes:
        orcamento = _orcamento_atual(manutencao)
        if not orcamento:
            return JSONResponse(
                {"ok": False, "erro": f"{codigo_tecnico(manutencao.equipamento)} está sem orçamento."},
                status_code=400,
            )
        if acao == "cancelar":
            orcamento.status = "Cancelado"
            manutencao.status = "Cancelado"
        else:
            registrar_aprovacao_orcamento(
                orcamento,
                "todos" if acao == "todos" else "obrigatorios",
                f"manual por {usuario.nome}",
            )

    db.commit()

    mensagens = {
        "obrigatorios": "Itens obrigatórios aprovados manualmente.",
        "todos": "Orçamento completo aprovado manualmente.",
        "cancelar": "Orçamento cancelado manualmente.",
    }
    return JSONResponse({
        "ok": True,
        "mensagem": mensagens[acao],
        "quantidade": len(manutencoes),
    })


@app.get("/organiza/operacao/pagamentos", response_class=HTMLResponse)
def operacao_pagamentos(request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    # Mantém visíveis todos os equipamentos da etapa 4, inclusive os já quitados
    # que ainda aguardam prazo e confirmação. Isso evita que desapareçam da
    # Central antes de avançarem para execução.
    pendentes = [m for m in _manutencoes_operacao(db) if etapa_manutencao(m) == 4]
    return templates.TemplateResponse("organiza/operacao_pagamentos.html", {
        "request": request, "usuario": usuario, "grupos": _agrupar_por_cliente(pendentes),
        "erro": request.query_params.get("erro", ""), "sucesso": request.query_params.get("sucesso", ""), "hoje": date.today().isoformat()
    })


@app.post("/organiza/operacao/pagamentos/registrar")
async def operacao_pagamentos_registrar(request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    form = await request.form()
    try:
        ids = [int(x) for x in form.getlist("manutencao_id")]
    except ValueError:
        ids = []

    selecionadas = [m for m in _manutencoes_operacao(db) if m.id in ids and etapa_manutencao(m) == 4]
    if not selecionadas:
        return RedirectResponse("/organiza/operacao/pagamentos?erro=Selecione pelo menos um equipamento", status_code=303)
    if len({m.cliente_id for m in selecionadas}) != 1:
        return RedirectResponse("/organiza/operacao/pagamentos?erro=Selecione equipamentos do mesmo cliente", status_code=303)

    previsao = data_form(form.get("previsao"))
    if not previsao:
        return RedirectResponse("/organiza/operacao/pagamentos?erro=Informe a previsão de conclusão", status_code=303)

    saldos = [(m, _orcamento_atual(m), _saldo_manutencao(m)[2]) for m in selecionadas]
    saldo_total = sum(s for _, _, s in saldos)

    # Se os equipamentos já estiverem quitados, esta ação serve apenas para
    # registrar a previsão, confirmar o cliente e liberar a execução.
    valor = moeda_num(form.get("valor")) if saldo_total > 0.009 else 0
    data_pagamento = data_form(form.get("data")) or date.today()
    forma = (form.get("forma") or "").strip()
    banco = forma
    observacao = (form.get("observacao") or "").strip()

    if saldo_total > 0.009 and valor <= 0:
        return RedirectResponse("/organiza/operacao/pagamentos?erro=Informe o valor recebido", status_code=303)
    if valor > saldo_total + 0.009:
        return RedirectResponse("/organiza/operacao/pagamentos?erro=O valor é maior que o saldo dos equipamentos selecionados", status_code=303)

    restante = max(valor, 0)
    for m, o, saldo in saldos:
        if restante <= 0.009 or not o or saldo <= 0:
            continue
        aplicado = min(restante, saldo)
        db.add(Pagamento(
            orcamento_id=o.id,
            data=data_pagamento,
            valor=round(aplicado, 2),
            forma=forma or None,
            banco=banco or None,
            observacao=(f"Pagamento agrupado. {observacao}".strip()),
        ))
        restante -= aplicado

    agora = datetime.now()
    prazo_texto = previsao.strftime("%d/%m/%Y")
    for m, _, _ in saldos:
        m.entrega_prevista_em = datetime.combine(previsao, time(17, 0))
        m.prazo = prazo_texto
        m.confirmacao_prazo_em = agora
        m.status = "Em manutenção"

    db.commit()

    # Confere a transição depois da gravação para nunca deixar o equipamento
    # entre a etapa de pagamento e a execução.
    ids_nao_liberados = []
    for manutencao_id in ids:
        atual = db.query(Manutencao).filter(Manutencao.id == manutencao_id).first()
        if atual and etapa_manutencao(atual) != 5:
            ids_nao_liberados.append(str(manutencao_id))
    if ids_nao_liberados:
        return RedirectResponse(
            "/organiza/operacao/pagamentos?erro=Não foi possível liberar alguns equipamentos para execução. Abra a manutenção e revise pagamento e previsão.",
            status_code=303,
        )

    linhas = [
        f"Olá, {selecionadas[0].cliente.nome}!",
        "",
        "✅ Pagamento e prazo confirmados.",
        "",
        "Equipamentos liberados para execução:",
    ]
    for m in selecionadas:
        linhas.append(f"• {rotulo_maquina(m.equipamento)} — previsão {prazo_texto}")
    linhas.extend(["", "Agora iniciaremos a execução dos serviços.", "", "Karaokê RJ"])
    mensagem = "\n".join(linhas)

    # Registra a mesma comunicação em cada manutenção selecionada e abre uma
    # única conversa do cliente, mantendo a operação agrupada.
    for m in selecionadas:
        ComunicacaoService.registrar(
            db, HistoricoComunicacao, m, usuario, "PRAZO",
            mensagem=mensagem,
        )

    url = ComunicacaoService.url_whatsapp(selecionadas[0].cliente, mensagem)
    return RedirectResponse(url, status_code=303)



# ---------------------------------------------------------
# CENTRAL FINANCEIRO -> CONNECT
# ---------------------------------------------------------

def _connect_configurado():
    return bool((os.getenv("CONNECT_API_URL") or "").strip())


def _connect_endpoint():
    base = (os.getenv("CONNECT_API_URL") or "").strip().rstrip("/")
    if base.endswith("/api/integracoes/organiza/lancamentos"):
        return base
    return base + "/api/integracoes/organiza/lancamentos"


def _payload_hash(payload: dict) -> str:
    bruto = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(bruto.encode("utf-8")).hexdigest()


def _enviar_para_connect(payload: dict):
    if not _connect_configurado():
        raise RuntimeError("CONNECT_API_URL não configurada.")
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    chave = (os.getenv("CONNECT_API_KEY") or os.getenv("ORGANIZA_API_KEY") or "").strip()
    if chave:
        headers["X-API-Key"] = chave
    req = urllib.request.Request(
        _connect_endpoint(),
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            corpo = resp.read().decode("utf-8", errors="replace")
            return json.loads(corpo) if corpo else {"ok": True}
    except urllib.error.HTTPError as exc:
        detalhe = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Connect respondeu HTTP {exc.code}: {detalhe[:500]}")
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Não foi possível acessar o Connect: {exc.reason}")


def _registro_integracao(db: Session, origem: str, registro_id: int):
    return db.query(IntegracaoConect).filter(
        IntegracaoConect.origem == origem,
        IntegracaoConect.registro_id == registro_id,
    ).first()


def _obs_pagamento_padrao(equipamento, cliente, nome_comprovante: str = "") -> str:
    partes = []
    if equipamento:
        partes.append(rotulo_maquina(equipamento))
    if cliente and getattr(cliente, "nome", None):
        partes.append(cliente.nome.strip())
    nome = (nome_comprovante or "").strip()
    if nome:
        partes.append(nome)
    return " - ".join([p for p in partes if p])


def _payload_venda(p: PagamentoVenda, db: Session, total_recebido_operacao=None):
    eq = p.equipamento
    cliente = eq.cliente if eq else None
    descricao = f"Venda {rotulo_maquina(eq)}" if eq else f"Venda #{p.equipamento_id}"

    total_operacao = moeda_num(eq.valor) if eq else 0.0
    total_recebido = total_recebido_operacao
    if total_recebido is None:
        total_recebido = sum(
            float(v or 0)
            for (v,) in db.query(PagamentoVenda.valor)
            .filter(PagamentoVenda.equipamento_id == p.equipamento_id)
            .all()
        )
    falta_receber = max(float(total_operacao) - total_recebido, 0.0)

    return {
        "id_externo": f"ORGANIZA-VENDA-PAG-{p.id}",
        "tipo": "venda",
        "cliente": cliente.nome if cliente else "",
        "descricao": descricao,
        "valor": round(float(p.valor or 0), 2),
        "falta_receber": round(falta_receber, 2),
        "data_pagamento": p.data.isoformat(),
        "banco": p.banco or p.forma or "",
        "observacao": p.observacao or "",
    }


def _payload_manutencao(p: Pagamento, db: Session):
    o = p.orcamento
    m = o.manutencao if o else None
    cliente = m.cliente if m else None
    equipamento = m.equipamento if m else None
    descricao = f"Manutenção {rotulo_maquina(equipamento)}" if equipamento else f"Manutenção #{getattr(m, 'id', p.id)}"

    falta_receber = 0.0
    if m:
        _, _, falta_receber = _saldo_manutencao(m)

    return {
        "id_externo": f"ORGANIZA-MANUTENCAO-PAG-{p.id}",
        "tipo": "manutencao",
        "cliente": cliente.nome if cliente else "",
        "descricao": descricao,
        "valor": round(float(p.valor or 0), 2),
        "falta_receber": round(float(falta_receber or 0), 2),
        "data_pagamento": p.data.isoformat(),
        "banco": p.banco or p.forma or "",
        "observacao": p.observacao or "",
    }


def _linhas_central_financeiro(db: Session):
    linhas = []

    vendas = db.query(PagamentoVenda).options(
        selectinload(PagamentoVenda.equipamento).selectinload(Equipamento.cliente)
    ).order_by(PagamentoVenda.data.desc(), PagamentoVenda.id.desc()).all()

    totais_venda = dict(
        db.query(PagamentoVenda.equipamento_id, func.sum(PagamentoVenda.valor))
        .group_by(PagamentoVenda.equipamento_id).all()
    )
    integracoes = {
        (i.origem, i.registro_id): i for i in db.query(IntegracaoConect).all()
    }

    for p in vendas:
        payload = _payload_venda(p, db, float(totais_venda.get(p.equipamento_id) or 0))
        integ = integracoes.get(("venda", p.id))
        hash_atual = _payload_hash(payload)
        status = (
            "ignorado" if integ and integ.ignorado
            else "enviado" if integ and integ.hash_conteudo == hash_atual and integ.enviado_em
            else "atualizado" if integ and integ.enviado_em
            else "novo"
        )
        linhas.append({"origem": "Venda", "registro": p, "payload": payload, "status_sync": status, "integracao": integ, "editar_url": f"/organiza/vendas/{p.equipamento_id}/pagamentos", "operacao_chave": f"venda:{p.equipamento_id}"})

    manutencoes = db.query(Pagamento).options(
        selectinload(Pagamento.orcamento).selectinload(Orcamento.manutencao).selectinload(Manutencao.cliente),
        selectinload(Pagamento.orcamento).selectinload(Orcamento.manutencao).selectinload(Manutencao.equipamento),
        selectinload(Pagamento.orcamento).selectinload(Orcamento.manutencao).selectinload(Manutencao.orcamentos).selectinload(Orcamento.itens),
        selectinload(Pagamento.orcamento).selectinload(Orcamento.manutencao).selectinload(Manutencao.orcamentos).selectinload(Orcamento.pagamentos),
    ).order_by(Pagamento.data.desc(), Pagamento.id.desc()).all()
    for p in manutencoes:
        payload = _payload_manutencao(p, db)
        integ = integracoes.get(("manutencao", p.id))
        hash_atual = _payload_hash(payload)
        status = (
            "ignorado" if integ and integ.ignorado
            else "enviado" if integ and integ.hash_conteudo == hash_atual and integ.enviado_em
            else "atualizado" if integ and integ.enviado_em
            else "novo"
        )
        linhas.append({"origem": "Manutenção", "registro": p, "payload": payload, "status_sync": status, "integracao": integ, "editar_url": f"/organiza/manutencoes/{p.orcamento.manutencao.id}#etapa-4", "operacao_chave": f"manutencao:{p.orcamento.manutencao_id}"})
    linhas.sort(key=lambda x: (x["registro"].data, x["registro"].id), reverse=True)
    return linhas


@app.get("/organiza/financeiro/conect", response_class=HTMLResponse)
def central_financeiro_conect(
    request: Request,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    linhas_todas = _linhas_central_financeiro(db)
    pendentes = sum(1 for l in linhas_todas if l["status_sync"] not in ("enviado", "ignorado"))
    filtro_status = (request.query_params.get("status") or "nao_enviados").strip().lower()
    if filtro_status == "enviados":
        linhas = [l for l in linhas_todas if l["status_sync"] == "enviado"]
    elif filtro_status == "todos":
        linhas = linhas_todas
    elif filtro_status == "ignorados":
        linhas = [l for l in linhas_todas if l["status_sync"] == "ignorado"]
    else:
        filtro_status = "nao_enviados"
        linhas = [l for l in linhas_todas if l["status_sync"] not in ("enviado", "ignorado")]

    # Evita gerar uma tabela HTML gigantesca. Mantém o filtro completo, mas
    # entrega somente uma página por vez ao navegador.
    total_filtrado = len(linhas)
    por_pagina = 100
    try:
        pagina = max(int(request.query_params.get("pagina") or 1), 1)
    except (TypeError, ValueError):
        pagina = 1
    total_paginas = max((total_filtrado + por_pagina - 1) // por_pagina, 1)
    pagina = min(pagina, total_paginas)
    inicio = (pagina - 1) * por_pagina
    linhas = linhas[inicio:inicio + por_pagina]

    return templates.TemplateResponse("organiza/central_financeiro_conect.html", {
        "request": request,
        "usuario": usuario,
        "linhas": linhas,
        "pendentes": pendentes,
        "filtro_status": filtro_status,
        "total_registros": len(linhas_todas),
        "total_filtrado": total_filtrado,
        "pagina": pagina,
        "total_paginas": total_paginas,
        "connect_configurado": _connect_configurado(),
        "sucesso": request.query_params.get("sucesso", ""),
        "erro": request.query_params.get("erro", ""),
    })


@app.post("/organiza/financeiro/conect/enviar")
def central_financeiro_conect_enviar(
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    if not _connect_configurado():
        return RedirectResponse("/organiza/financeiro/conect?erro=Configure CONNECT_API_URL no ambiente.", status_code=303)

    linhas = _linhas_central_financeiro(db)
    enviar = [l for l in linhas if l["status_sync"] not in ("enviado", "ignorado")]
    enviados = 0
    erros = []
    for linha in enviar:
        payload = linha["payload"]
        try:
            resposta = _enviar_para_connect(payload)
            origem = "venda" if linha["origem"] == "Venda" else "manutencao"
            integ = _registro_integracao(db, origem, linha["registro"].id)
            if not integ:
                integ = IntegracaoConect(
                    origem=origem,
                    registro_id=linha["registro"].id,
                    id_externo=payload["id_externo"],
                )
                db.add(integ)
            integ.hash_conteudo = _payload_hash(payload)
            integ.enviado_em = datetime.now()
            integ.ignorado = 0
            integ.resposta = json.dumps(resposta, ensure_ascii=False)[:4000]
            db.commit()
            enviados += 1
        except Exception as exc:
            db.rollback()
            erros.append(f'{payload["id_externo"]}: {str(exc)}')
            break

    if erros:
        msg = quote_plus(f"{enviados} enviado(s). Erro: {erros[0]}")
        return RedirectResponse(f"/organiza/financeiro/conect?erro={msg}", status_code=303)
    msg = quote_plus(f"{enviados} lançamento(s) enviado(s) ao Connect." if enviados else "Tudo já estava sincronizado.")
    return RedirectResponse(f"/organiza/financeiro/conect?sucesso={msg}", status_code=303)



def _chave_linha_connect(linha) -> str:
    origem = "venda" if linha["origem"] == "Venda" else "manutencao"
    return f"{origem}:{linha['registro'].id}"


@app.post("/organiza/financeiro/conect/enviar-selecionados")
async def central_financeiro_conect_enviar_selecionados(
    request: Request,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    if not _connect_configurado():
        return RedirectResponse("/organiza/financeiro/conect?erro=Configure CONNECT_API_URL no ambiente.", status_code=303)

    form = await request.form()
    selecionados = set(form.getlist("selecionados"))
    if not selecionados:
        return RedirectResponse("/organiza/financeiro/conect?erro=Selecione pelo menos um lançamento.", status_code=303)

    linhas = [l for l in _linhas_central_financeiro(db) if _chave_linha_connect(l) in selecionados]
    enviados = 0
    for linha in linhas:
        payload = linha["payload"]
        try:
            resposta = _enviar_para_connect(payload)
            origem = "venda" if linha["origem"] == "Venda" else "manutencao"
            integ = _registro_integracao(db, origem, linha["registro"].id)
            if not integ:
                integ = IntegracaoConect(
                    origem=origem,
                    registro_id=linha["registro"].id,
                    id_externo=payload["id_externo"],
                )
                db.add(integ)
            integ.hash_conteudo = _payload_hash(payload)
            integ.enviado_em = datetime.now()
            integ.resposta = json.dumps(resposta, ensure_ascii=False)[:4000]
            integ.ignorado = 0
            db.commit()
            enviados += 1
        except Exception as exc:
            db.rollback()
            msg = quote_plus(f"{enviados} enviado(s). Erro em {payload['id_externo']}: {str(exc)}")
            return RedirectResponse(f"/organiza/financeiro/conect?erro={msg}", status_code=303)

    msg = quote_plus(f"{enviados} lançamento(s) selecionado(s) enviado(s) ao Connect.")
    return RedirectResponse(f"/organiza/financeiro/conect?sucesso={msg}", status_code=303)



def _normalizar_texto_agrupamento(valor) -> str:
    return re.sub(r"\s+", " ", (str(valor or "").strip().lower()))


def _grupos_connect_selecionados(linhas):
    """
    Agrupa somente lançamentos compatíveis com um único lançamento no Connect:
    mesmo tipo, cliente, data de pagamento e banco.
    """
    grupos = {}
    for linha in linhas:
        payload = linha["payload"]
        chave = (
            payload["tipo"],
            _normalizar_texto_agrupamento(payload.get("cliente")),
            payload["data_pagamento"],
            _normalizar_texto_agrupamento(payload.get("banco")),
        )
        grupos.setdefault(chave, []).append(linha)
    return list(grupos.values())


def _payload_grupo_connect(grupo):
    primeiro = grupo[0]["payload"]
    chaves_origem = sorted(_chave_linha_connect(l) for l in grupo)
    assinatura = hashlib.sha256("|".join(chaves_origem).encode("utf-8")).hexdigest()[:16]
    tipo = primeiro["tipo"]
    cliente = primeiro.get("cliente") or ""
    quantidade = len(grupo)
    valor_total = round(sum(float(l["payload"].get("valor") or 0) for l in grupo), 2)

    # O saldo é apenas informativo. Pagamentos da mesma operação
    # contam o saldo dessa operação uma única vez.
    saldos_por_operacao = {}
    for linha in grupo:
        chave_operacao = linha.get("operacao_chave") or _chave_linha_connect(linha)
        saldos_por_operacao[chave_operacao] = float(linha["payload"].get("falta_receber") or 0)
    falta_receber = round(sum(saldos_por_operacao.values()), 2)

    rotulo_tipo = "Venda" if tipo == "venda" else "Manutenção"
    descricao = f"{rotulo_tipo} agrupada - {quantidade} lançamento(s)"
    if cliente:
        descricao += f" - {cliente}"

    return {
        "id_externo": f"ORGANIZA-GRUPO-{tipo.upper()}-{assinatura}",
        "tipo": tipo,
        "cliente": cliente,
        "descricao": descricao,
        "valor": valor_total,
        "falta_receber": falta_receber,
        "data_pagamento": primeiro["data_pagamento"],
        "banco": primeiro.get("banco") or "",
    }


@app.post("/organiza/financeiro/conect/enviar-agrupado")
async def central_financeiro_conect_enviar_agrupado(
    request: Request,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    if not _connect_configurado():
        return RedirectResponse(
            "/organiza/financeiro/conect?erro=Configure CONNECT_API_URL no ambiente.",
            status_code=303,
        )

    form = await request.form()
    selecionados = set(form.getlist("selecionados"))
    if not selecionados:
        return RedirectResponse(
            "/organiza/financeiro/conect?erro=Selecione pelo menos um lançamento para agrupar.",
            status_code=303,
        )

    linhas = [
        l for l in _linhas_central_financeiro(db)
        if _chave_linha_connect(l) in selecionados
    ]
    if not linhas:
        return RedirectResponse(
            "/organiza/financeiro/conect?erro=Nenhum lançamento válido foi selecionado.",
            status_code=303,
        )

    grupos = _grupos_connect_selecionados(linhas)
    enviados = 0

    for grupo in grupos:
        payload_grupo = _payload_grupo_connect(grupo)

        try:
            resposta = _enviar_para_connect(payload_grupo)

            # Cada origem continua controlada individualmente no Organiza,
            # embora o Connect receba apenas um lançamento com o total agrupado.
            for linha in grupo:
                origem = "venda" if linha["origem"] == "Venda" else "manutencao"
                payload_individual = linha["payload"]
                integ = _registro_integracao(db, origem, linha["registro"].id)

                if not integ:
                    integ = IntegracaoConect(
                        origem=origem,
                        registro_id=linha["registro"].id,
                        id_externo=payload_individual["id_externo"],
                    )
                    db.add(integ)

                integ.hash_conteudo = _payload_hash(payload_individual)
                integ.enviado_em = datetime.now()
                integ.ignorado = 0
                integ.resposta = json.dumps({
                    "modo": "agrupado",
                    "id_externo_grupo": payload_grupo["id_externo"],
                    "valor_grupo": payload_grupo["valor"],
                    "quantidade_grupo": len(grupo),
                    "resposta_connect": resposta,
                }, ensure_ascii=False)[:4000]

            db.commit()
            enviados += 1

        except Exception as exc:
            db.rollback()
            msg = quote_plus(
                f"{enviados} grupo(s) enviado(s). Erro no grupo "
                f"{payload_grupo['cliente']}: {str(exc)}"
            )
            return RedirectResponse(
                f"/organiza/financeiro/conect?erro={msg}",
                status_code=303,
            )

    total_origens = len(linhas)
    msg = quote_plus(
        f"{total_origens} lançamento(s) agrupado(s) em "
        f"{enviados} lançamento(s) enviado(s) ao Connect."
    )
    return RedirectResponse(
        f"/organiza/financeiro/conect?sucesso={msg}",
        status_code=303,
    )


@app.post("/organiza/financeiro/conect/nao-enviar")
async def central_financeiro_conect_nao_enviar(
    request: Request,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    form = await request.form()
    selecionados = set(form.getlist("selecionados"))
    if not selecionados:
        return RedirectResponse("/organiza/financeiro/conect?erro=Selecione pelo menos um lançamento.", status_code=303)

    alterados = 0
    for linha in _linhas_central_financeiro(db):
        if _chave_linha_connect(linha) not in selecionados:
            continue
        origem = "venda" if linha["origem"] == "Venda" else "manutencao"
        integ = _registro_integracao(db, origem, linha["registro"].id)
        if not integ:
            integ = IntegracaoConect(
                origem=origem,
                registro_id=linha["registro"].id,
                id_externo=linha["payload"]["id_externo"],
            )
            db.add(integ)
        integ.ignorado = 1
        alterados += 1
    db.commit()
    msg = quote_plus(f"{alterados} lançamento(s) marcado(s) como 'Não enviar'.")
    return RedirectResponse(f"/organiza/financeiro/conect?sucesso={msg}", status_code=303)


@app.get("/organiza/vendas/{equipamento_id}/pagamentos", response_class=HTMLResponse)
def venda_pagamentos(
    equipamento_id: int,
    request: Request,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    eq = db.query(Equipamento).options(selectinload(Equipamento.cliente)).filter(Equipamento.id == equipamento_id).first()
    if not eq or not equipamento_eh_venda(eq):
        raise HTTPException(404)
    pagamentos = db.query(PagamentoVenda).filter(PagamentoVenda.equipamento_id == equipamento_id).order_by(PagamentoVenda.data.desc(), PagamentoVenda.id.desc()).all()
    total = moeda_num(eq.valor)
    recebido = sum(float(p.valor or 0) for p in pagamentos)
    return templates.TemplateResponse("organiza/venda_pagamentos.html", {
        "request": request, "usuario": usuario, "venda": eq, "pagamentos": pagamentos,
        "total": total, "recebido": recebido, "saldo": max(total - recebido, 0),
        "hoje": date.today().isoformat(), "erro": request.query_params.get("erro", ""),
        "observacao_padrao": _obs_pagamento_padrao(eq, eq.cliente),
    })


@app.post("/organiza/vendas/{equipamento_id}/pagamentos")
async def venda_pagamento_registrar(
    equipamento_id: int,
    request: Request,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    eq = db.query(Equipamento).filter(Equipamento.id == equipamento_id).first()
    if not eq or not equipamento_eh_venda(eq):
        raise HTTPException(404)
    form = dict(await request.form())
    valor = moeda_num(form.get("valor"))
    data_pag = data_form(form.get("data")) or date.today()
    forma = (form.get("forma") or "PIX").strip()
    banco = forma
    nome_comprovante = (form.get("observacao") or "").strip()
    observacao = _obs_pagamento_padrao(eq, eq.cliente, nome_comprovante)
    nao_enviar_connect = bool(form.get("nao_enviar_connect"))
    if valor <= 0:
        return RedirectResponse(f"/organiza/vendas/{equipamento_id}/pagamentos?erro=Informe um valor válido.", status_code=303)
    total = moeda_num(eq.valor)
    recebido = sum(float(p.valor or 0) for p in db.query(PagamentoVenda).filter(PagamentoVenda.equipamento_id == equipamento_id).all())
    saldo = round(total - recebido, 2)
    if total <= 0:
        return RedirectResponse(f"/organiza/vendas/{equipamento_id}/pagamentos?erro=A venda não possui um valor total válido. Corrija a venda antes de registrar pagamentos.", status_code=303)
    if saldo <= 0.009:
        return RedirectResponse(f"/organiza/vendas/{equipamento_id}/pagamentos?erro=Esta venda já está totalmente paga.", status_code=303)
    if valor > saldo + 0.009:
        return RedirectResponse(f"/organiza/vendas/{equipamento_id}/pagamentos?erro=O pagamento não pode ser maior que o saldo da venda.", status_code=303)
    pagamento = PagamentoVenda(
        equipamento_id=equipamento_id, data=data_pag, valor=round(valor, 2),
        banco=banco, forma=forma, observacao=observacao or None,
    )
    db.add(pagamento)
    db.flush()
    if nao_enviar_connect:
        db.add(IntegracaoConect(
            origem="venda", registro_id=pagamento.id,
            id_externo=f"ORGANIZA-VENDA-PAG-{pagamento.id}",
            ignorado=1,
        ))
    db.commit()
    return RedirectResponse(f"/organiza/vendas/{equipamento_id}/pagamentos", status_code=303)



@app.post("/organiza/vendas/{equipamento_id}/pagamentos/{pagamento_id}/editar")
async def venda_pagamento_editar(
    equipamento_id: int,
    pagamento_id: int,
    request: Request,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    p = db.query(PagamentoVenda).filter(
        PagamentoVenda.id == pagamento_id,
        PagamentoVenda.equipamento_id == equipamento_id,
    ).first()
    if not p:
        raise HTTPException(404)

    form = dict(await request.form())
    valor = moeda_num(form.get("valor"))
    forma = (form.get("forma") or "").strip()
    banco = forma
    data_pag = data_form(form.get("data") or "")
    if valor <= 0 or not data_pag or not forma:
        return RedirectResponse(
            f"/organiza/vendas/{equipamento_id}/pagamentos?erro=Informe data, valor e banco válidos.",
            status_code=303,
        )

    eq = db.query(Equipamento).filter(Equipamento.id == equipamento_id).first()
    total = moeda_num(eq.valor if eq else 0)
    outros = sum(float(item.valor or 0) for item in db.query(PagamentoVenda).filter(
        PagamentoVenda.equipamento_id == equipamento_id, PagamentoVenda.id != pagamento_id
    ).all())
    if total <= 0 or valor > round(total - outros, 2) + 0.009:
        return RedirectResponse(
            f"/organiza/vendas/{equipamento_id}/pagamentos?erro=O valor informado ultrapassa o saldo disponível da venda.",
            status_code=303,
        )

    p.valor = round(valor, 2)
    p.data = data_pag
    p.forma = forma
    p.banco = banco
    nome_comprovante = (form.get("observacao") or "").strip()
    prefixo = _obs_pagamento_padrao(eq, eq.cliente if eq else None)
    p.observacao = (nome_comprovante if nome_comprovante.startswith(prefixo) else _obs_pagamento_padrao(eq, eq.cliente if eq else None, nome_comprovante)) or None
    db.commit()
    return RedirectResponse(f"/organiza/vendas/{equipamento_id}/pagamentos", status_code=303)


@app.post("/organiza/vendas/{equipamento_id}/pagamentos/{pagamento_id}/excluir")
def venda_pagamento_excluir(
    equipamento_id: int,
    pagamento_id: int,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    p = db.query(PagamentoVenda).filter(PagamentoVenda.id == pagamento_id, PagamentoVenda.equipamento_id == equipamento_id).first()
    if not p:
        raise HTTPException(404)
    integ = _registro_integracao(db, "venda", p.id)
    if integ and integ.enviado_em:
        return RedirectResponse(f"/organiza/vendas/{equipamento_id}/pagamentos?erro=Pagamento já enviado ao Connect. Ajuste o registro em vez de excluir.", status_code=303)
    if integ:
        db.delete(integ)
    db.delete(p)
    db.commit()
    return RedirectResponse(f"/organiza/vendas/{equipamento_id}/pagamentos", status_code=303)


@app.get("/organiza/agenda", response_class=HTMLResponse)
def agenda(request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    manutencoes = (
        db.query(Manutencao)
        .options(
            selectinload(Manutencao.cliente),
            selectinload(Manutencao.equipamento),
            selectinload(Manutencao.orcamentos).selectinload(Orcamento.pagamentos),
        )
        .filter(
            Manutencao.entregue_em.is_(None),
            ~Manutencao.status.in_(("Encerrada", "Cancelada")),
            or_(
                Manutencao.entrega_prevista_em.isnot(None),
                Manutencao.retirada_em.isnot(None),
                Manutencao.pronto_em.isnot(None),
                Manutencao.status.in_(("Pronto para retirada", "Retirada agendada")),
            ),
        )
        .all()
    )

    eventos = []
    for m in manutencoes:
        etapa = etapa_manutencao(m)
        if etapa == 1 and m.entrega_prevista_em:
            online = (m.tipo_atendimento or "loja") == "online"
            eventos.append({
                "tipo": "online" if online else "entrada",
                "titulo": "Atendimento online" if online else "Cliente vai trazer",
                "data_hora": m.entrega_prevista_em,
                "cliente": m.cliente.nome,
                "equipamento": descricao_equipamento(m.equipamento),
                "link": f"/organiza/manutencoes/{m.id}#etapa-1",
                "manual": False,
                "manutencao_id": m.id,
                "agendamento_tipo": "entrada",
            })
        elif etapa == 6 and m.retirada_em:
            eventos.append({
                "tipo": "retirada",
                "titulo": "Cliente vem buscar",
                "data_hora": m.retirada_em,
                "cliente": m.cliente.nome,
                "equipamento": descricao_equipamento(m.equipamento),
                "link": f"/organiza/manutencoes/{m.id}#etapa-6",
                "manual": False,
                "manutencao_id": m.id,
                "agendamento_tipo": "retirada",
            })

    for e in db.query(AgendaManual).order_by(AgendaManual.data_hora.asc()).all():
        eventos.append({
            "tipo": e.tipo,
            "titulo": e.titulo,
            "data_hora": e.data_hora,
            "cliente": e.contato or "Compromisso manual",
            "equipamento": e.observacao or "",
            "link": f"/organiza/agenda/manual/{e.id}/editar",
            "manual": True,
            "evento_id": e.id,
        })

    eventos.sort(key=lambda e: (e["data_hora"], e["titulo"]))
    return templates.TemplateResponse("organiza/agenda.html", {
        "request": request, "usuario": usuario, "eventos": eventos,
    })



@app.get("/agendamento/{token}/{manutencao_id}", response_class=HTMLResponse)
def agendamento_cliente_publico(
    token: str,
    manutencao_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    cliente = db.query(Cliente).filter(Cliente.token_ficha == token).first()
    if not cliente:
        raise HTTPException(404)
    m = db.query(Manutencao).filter(
        Manutencao.id == manutencao_id,
        Manutencao.cliente_id == cliente.id,
    ).first()
    if not m:
        raise HTTPException(404)
    return templates.TemplateResponse("organiza/agendamento_cliente_publico.html", {
        "request": request,
        "cliente": cliente,
        "m": m,
        "erro": request.query_params.get("erro", ""),
        "ok": request.query_params.get("ok", ""),
        "hoje": date.today().isoformat(),
    })


@app.post("/agendamento/{token}/{manutencao_id}")
async def agendamento_cliente_publico_salvar(
    token: str,
    manutencao_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    cliente = db.query(Cliente).filter(Cliente.token_ficha == token).first()
    if not cliente:
        raise HTTPException(404)
    m = db.query(Manutencao).filter(
        Manutencao.id == manutencao_id,
        Manutencao.cliente_id == cliente.id,
    ).first()
    if not m:
        raise HTTPException(404)
    form = await request.form()
    acao = (form.get("acao") or "reagendar").strip()

    if acao == "cancelar":
        m.status = "Cancelada"
        m.entrega_prevista_em = None
        db.commit()
        return RedirectResponse(f"/agendamento/{token}/{manutencao_id}?ok=cancelado", status_code=303)

    nova_data = datetime_form(form.get("data_hora") or "")
    if not nova_data or nova_data < datetime.now():
        return RedirectResponse(
            f"/agendamento/{token}/{manutencao_id}?erro=Escolha uma data e horário futuros.",
            status_code=303,
        )
    if horario_atendimento_ocupado(db, nova_data, m.id):
        return RedirectResponse(
            f"/agendamento/{token}/{manutencao_id}?erro=Este horário não está disponível. Escolha outro.",
            status_code=303,
        )
    m.entrega_prevista_em = nova_data
    m.status = "Aguardando equipamento"
    db.commit()
    return RedirectResponse(f"/agendamento/{token}/{manutencao_id}?ok=reagendado", status_code=303)


@app.get("/organiza/manutencoes/{manutencao_id}/nao-compareceu-whatsapp")
def manutencao_nao_compareceu_whatsapp(
    manutencao_id: int,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    m = carregar_manutencao(db, manutencao_id)
    if not m:
        raise HTTPException(404)
    cliente = m.cliente
    if not cliente.token_ficha:
        cliente.token_ficha = secrets.token_urlsafe(24)
        db.commit()
    link = f"{PUBLIC_BASE_URL}/agendamento/{cliente.token_ficha}/{m.id}"
    mensagem = (
        f"Olá, {cliente.nome}!\\n\\n"
        "Hoje estava prevista a entrega/atendimento do seu equipamento, mas não conseguimos concluir o recebimento.\\n\\n"
        f"Equipamento: {descricao_equipamento(m.equipamento)}\\n"
        f"Ordem de serviço: #{m.id}\\n\\n"
        "Para não deixar uma pendência em aberto, escolha uma opção no link abaixo:\\n"
        "• Reagendar uma nova data e horário\\n"
        "• Cancelar esta solicitação\\n\\n"
        f"{link}\\n\\nKaraokê RJ"
    )
    url = ComunicacaoService.registrar_e_url(db, HistoricoComunicacao, m, usuario, "NAO_COMPARECEU", mensagem)
    return RedirectResponse(url, status_code=303)


@app.post("/organiza/agenda/manutencao/{manutencao_id}/reagendar")
async def agenda_manutencao_reagendar(manutencao_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    m = db.query(Manutencao).filter(Manutencao.id == manutencao_id).first()
    if not m:
        raise HTTPException(404)
    form = await request.form()
    nova_data = datetime_form(form.get("data_hora") or "")
    tipo = (form.get("tipo") or "entrada").strip()
    if not nova_data:
        return RedirectResponse("/organiza/agenda?erro=Informe uma nova data e horário.", status_code=303)
    if tipo == "retirada":
        m.retirada_em = nova_data
        m.status = "Retirada agendada"
    else:
        if horario_atendimento_ocupado(db, nova_data, m.id):
            return RedirectResponse("/organiza/agenda?erro=Este horário já está ocupado.", status_code=303)
        m.entrega_prevista_em = nova_data
        if not m.recebido_em:
            m.status = "Aguardando equipamento"
    db.commit()
    return RedirectResponse("/organiza/agenda", status_code=303)


@app.post("/organiza/agenda/manutencao/{manutencao_id}/excluir")
def agenda_manutencao_excluir(manutencao_id: int, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    m = db.query(Manutencao).filter(Manutencao.id == manutencao_id).first()
    if not m:
        raise HTTPException(404)
    if etapa_manutencao(m) == 1 and not m.recebido_em:
        # Cliente não trouxe o equipamento: encerra a pendência sem apagar o histórico.
        m.status = "Cancelada"
        m.entrega_prevista_em = None
    elif etapa_manutencao(m) == 6:
        # Remove apenas a retirada agendada; a manutenção continua pronta para retirada.
        m.retirada_em = None
        m.status = "Pronto para retirada"
    db.commit()
    return RedirectResponse("/organiza/agenda", status_code=303)


@app.get("/organiza/agenda/manual/novo", response_class=HTMLResponse)
def agenda_manual_novo(request: Request, usuario: Usuario = Depends(usuario_logado)):
    return templates.TemplateResponse("organiza/agenda_manual_form.html", {
        "request": request, "usuario": usuario, "evento": None, "erro": "",
    })


@app.post("/organiza/agenda/manual/novo")
async def agenda_manual_criar(request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    form = await request.form()
    data_hora = datetime_form(form.get("data_hora"))
    titulo = (form.get("titulo") or "").strip()
    if not titulo or not data_hora:
        return templates.TemplateResponse("organiza/agenda_manual_form.html", {
            "request": request, "usuario": usuario, "evento": None,
            "erro": "Informe o título e a data com horário.",
        }, status_code=400)
    db.add(AgendaManual(
        titulo=titulo, tipo=(form.get("tipo") or "visita").strip(),
        data_hora=data_hora, contato=(form.get("contato") or "").strip() or None,
        observacao=(form.get("observacao") or "").strip() or None,
    ))
    db.commit()
    return RedirectResponse("/organiza/agenda", status_code=303)


@app.get("/organiza/agenda/manual/{evento_id}/editar", response_class=HTMLResponse)
def agenda_manual_editar(evento_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    evento = db.get(AgendaManual, evento_id)
    if not evento:
        raise HTTPException(404)
    return templates.TemplateResponse("organiza/agenda_manual_form.html", {
        "request": request, "usuario": usuario, "evento": evento, "erro": "",
    })


@app.post("/organiza/agenda/manual/{evento_id}/editar")
async def agenda_manual_salvar(evento_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    evento = db.get(AgendaManual, evento_id)
    if not evento:
        raise HTTPException(404)
    form = await request.form()
    data_hora = datetime_form(form.get("data_hora"))
    titulo = (form.get("titulo") or "").strip()
    if not titulo or not data_hora:
        return templates.TemplateResponse("organiza/agenda_manual_form.html", {
            "request": request, "usuario": usuario, "evento": evento,
            "erro": "Informe o título e a data com horário.",
        }, status_code=400)
    evento.titulo = titulo
    evento.tipo = (form.get("tipo") or "visita").strip()
    evento.data_hora = data_hora
    evento.contato = (form.get("contato") or "").strip() or None
    evento.observacao = (form.get("observacao") or "").strip() or None
    db.commit()
    return RedirectResponse("/organiza/agenda", status_code=303)


@app.post("/organiza/agenda/manual/{evento_id}/excluir")
def agenda_manual_excluir(evento_id: int, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    evento = db.get(AgendaManual, evento_id)
    if evento:
        db.delete(evento)
        db.commit()
    return RedirectResponse("/organiza/agenda", status_code=303)


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



def _ids_selecionados_publicos(ids_texto: str) -> list[int]:
    try:
        return sorted({int(x) for x in (ids_texto or "").split(",") if x.strip()})
    except ValueError:
        raise HTTPException(400, "Seleção inválida.")


def _manutencoes_publicas_selecionadas(db: Session, cliente: Cliente, ids_texto: str, somente_prontas: bool = True):
    ids = _ids_selecionados_publicos(ids_texto)
    if not ids:
        raise HTTPException(400, "Selecione ao menos um equipamento.")
    consulta = (
        db.query(Manutencao)
        .options(
            selectinload(Manutencao.cliente),
            selectinload(Manutencao.equipamento),
            selectinload(Manutencao.orcamentos).selectinload(Orcamento.itens),
        )
        .filter(Manutencao.cliente_id == cliente.id, Manutencao.id.in_(ids))
    )
    if somente_prontas:
        consulta = consulta.filter(Manutencao.pronto_em.isnot(None), Manutencao.entregue_em.is_(None))
    manutencoes = consulta.order_by(Manutencao.id.asc()).all()
    if len(manutencoes) != len(ids):
        raise HTTPException(400, "Um ou mais equipamentos selecionados não estão disponíveis.")
    return manutencoes


@app.get("/garantias/{token}.pdf")
def garantias_agrupadas_pdf(token: str, ids: str, db: Session = Depends(get_db)):
    cliente = db.query(Cliente).filter(Cliente.token_ficha == token).first()
    if not cliente:
        raise HTTPException(404)
    manutencoes = _manutencoes_publicas_selecionadas(db, cliente, ids, somente_prontas=True)

    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_CENTER
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image, Table, TableStyle, PageBreak
    from reportlab.lib import colors

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=18*mm, leftMargin=18*mm, topMargin=15*mm, bottomMargin=15*mm)
    estilos = getSampleStyleSheet()
    titulo = ParagraphStyle("TituloGarantiaGrupo", parent=estilos["Title"], alignment=TA_CENTER, fontSize=18, leading=22, spaceAfter=8)
    centro = ParagraphStyle("CentroGarantiaGrupo", parent=estilos["BodyText"], alignment=TA_CENTER, fontSize=10, leading=14)
    corpo = ParagraphStyle("CorpoGarantiaGrupo", parent=estilos["BodyText"], fontSize=10, leading=15, spaceAfter=8)
    elementos = []
    logo_path = os.path.join(os.path.dirname(__file__), "static", "img", "logo-karaoke-rj.png")
    if not os.path.exists(logo_path):
        logo_path = os.path.join(os.path.dirname(__file__), "static", "img", "karaoke-rj-garantia.jpeg")

    for indice, m in enumerate(manutencoes):
        if indice:
            elementos.append(PageBreak())
        logo = Image(logo_path, width=30*mm, height=22*mm) if os.path.exists(logo_path) else Spacer(30*mm, 22*mm)
        empresa = Paragraph(
            "<b>KARAOKE &amp; GAMES RJ</b><br/>CNPJ: 35.458.112/0001-75 · IM: 1213508-4<br/>"
            "Rua João Romariz, 313 - Ramos - Rio de Janeiro/RJ - CEP: 21031-700<br/>"
            "WhatsApp: (21) 99507-9690 / (21) 99650-4516<br/>www.karaokerj.com.br · contato@karaokerj.com.br",
            ParagraphStyle(f"CabecalhoGarantiaGrupo{indice}", parent=corpo, fontSize=8, leading=10, spaceAfter=0),
        )
        header = Table([[logo, empresa]], colWidths=[35*mm, 139*mm])
        header.setStyle(TableStyle([
            ("VALIGN",(0,0),(-1,-1),"MIDDLE"), ("LINEBELOW",(0,0),(-1,-1),0.8,colors.HexColor("#555555")),
            ("LEFTPADDING",(0,0),(-1,-1),0), ("RIGHTPADDING",(0,0),(-1,-1),0), ("BOTTOMPADDING",(0,0),(-1,-1),5),
        ]))
        elementos += [header, Spacer(1, 7), Paragraph("CERTIFICADO DE GARANTIA DO SERVIÇO", titulo), Spacer(1, 4*mm)]
        eq = m.equipamento
        dados = [
            ["Ordem de serviço", f"#{m.id}"],
            ["Cliente", cliente.nome],
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
        elementos += [
            tabela, Spacer(1, 8*mm),
            Paragraph("<b>Garantia de 30 dias</b>", corpo),
            Paragraph(
                "A garantia cobre exclusivamente os serviços executados e os itens descritos na ordem de serviço. "
                "Não cobre mau uso, quedas, líquidos, ligação em tensão incorreta, intervenção de terceiros ou defeitos diferentes do serviço realizado.",
                corpo,
            ),
        ]
    doc.build(elementos)
    buffer.seek(0)
    return Response(
        buffer.getvalue(),
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="garantias-{cliente.id}.pdf"'},
    )


@app.get("/retirada-cliente/{token}", response_class=HTMLResponse)
def retirada_cliente_publica(token: str, ids: str, request: Request, db: Session = Depends(get_db)):
    cliente = db.query(Cliente).filter(Cliente.token_ficha == token).first()
    if not cliente:
        raise HTTPException(404)
    manutencoes = _manutencoes_publicas_selecionadas(db, cliente, ids, somente_prontas=True)
    agendamento_atual = next((m.retirada_em for m in manutencoes if m.retirada_em), None)
    return templates.TemplateResponse("organiza/retirada_cliente_publica.html", {
        "request": request,
        "cliente": cliente,
        "manutencoes": manutencoes,
        "ids": ids,
        "hoje": date.today().isoformat(),
        "erro": "",
        "agendamento_atual": agendamento_atual,
    })


@app.post("/retirada-cliente/{token}")
async def retirada_cliente_publica_salvar(token: str, request: Request, db: Session = Depends(get_db)):
    cliente = db.query(Cliente).filter(Cliente.token_ficha == token).first()
    if not cliente:
        raise HTTPException(404)
    form = await request.form()
    ids = (form.get("ids") or "").strip()
    manutencoes = _manutencoes_publicas_selecionadas(db, cliente, ids, somente_prontas=True)
    data_retirada = data_form(form.get("data_retirada") or "")
    hora_texto = (form.get("hora_retirada") or "").strip()
    dt = None
    try:
        hora_retirada = datetime.strptime(hora_texto, "%H:%M").time()
        if data_retirada:
            dt = datetime.combine(data_retirada, hora_retirada)
    except ValueError:
        pass

    agora = datetime.now()
    if dt and dt >= agora and dt.weekday() < 5 and time(14, 0) <= dt.time() <= time(17, 0):
        for manutencao in manutencoes:
            manutencao.retirada_em = dt
            manutencao.status = "Retirada agendada"
        db.commit()
        return RedirectResponse(f"/retirada-cliente/{token}?ids={ids}&ok=1", status_code=303)

    agendamento_atual = next((m.retirada_em for m in manutencoes if m.retirada_em), None)
    return templates.TemplateResponse("organiza/retirada_cliente_publica.html", {
        "request": request,
        "cliente": cliente,
        "manutencoes": manutencoes,
        "ids": ids,
        "hoje": date.today().isoformat(),
        "erro": "Escolha uma data e um horário válidos, de segunda a sexta, entre 14:00 e 17:00.",
        "agendamento_atual": agendamento_atual,
    }, status_code=400)


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

def linha_tempo_publica(m):
    etapa_atual = etapa_manutencao(m)
    datas = {
        1: m.entrega_prevista_em or m.criado_em,
        2: m.recebido_em,
        3: None,
        4: None,
        5: m.confirmacao_prazo_em,
        6: m.pronto_em or m.retirada_em,
        7: m.entregue_em,
    }
    o = sorted(m.orcamentos, key=lambda x: x.versao)[-1] if m.orcamentos else None
    if o:
        datas[3] = o.criado_em
        datas[4] = o.aprovado_em
    itens = []
    for numero in range(1, 8):
        dados = ETAPAS_MANUTENCAO[numero]
        itens.append({
            "numero": numero,
            "titulo": dados["titulo"],
            "rotulo": dados["rotulo"],
            "classe": dados["classe"],
            "data": datas.get(numero),
            "concluida": numero < etapa_atual,
            "atual": numero == etapa_atual,
            "bloqueada": numero > etapa_atual,
        })
    return itens


@app.get("/orcamento/{token}", response_class=HTMLResponse)
def orcamento_publico(token: str, request: Request, db: Session = Depends(get_db)):
    o = db.query(Orcamento).options(selectinload(Orcamento.itens), selectinload(Orcamento.manutencao).selectinload(Manutencao.cliente), selectinload(Orcamento.manutencao).selectinload(Manutencao.equipamento)).filter(Orcamento.token == token).first()
    if not o: raise HTTPException(404)
    return templates.TemplateResponse("organiza/orcamento_publico.html", {
        "request": request,
        "orcamento": o,
        "m": o.manutencao,
        "totais": totais_orcamento(o),
        "linha_tempo": linha_tempo_publica(o.manutencao),
        "etapa_atual": etapa_manutencao(o.manutencao),
    })


@app.post("/orcamento/{token}/responder")
async def orcamento_responder(token: str, request: Request, db: Session = Depends(get_db)):
    o = db.query(Orcamento).options(selectinload(Orcamento.itens), selectinload(Orcamento.manutencao)).filter(Orcamento.token == token).first(); form = dict(await request.form())
    if not o: raise HTTPException(404)
    acao = form.get("acao")
    if acao == "cancelar":
        o.status = "Cancelado"
        o.manutencao.status = "Cancelado"
    else:
        modalidade = "todos" if acao == "aprovar" else "obrigatorios"
        registrar_aprovacao_orcamento(o, modalidade, "cliente")
    db.commit()
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        if acao == "cancelar":
            mensagem = "Orçamento cancelado."
            status = "Cancelado"
        elif acao == "aprovar":
            mensagem = "Orçamento completo aprovado."
            status = "Tudo aprovado"
        else:
            mensagem = "Itens obrigatórios aprovados."
            status = "Obrigatórios aprovados"
        return JSONResponse({"ok": True, "mensagem": mensagem, "status": status})
    return RedirectResponse(f"/orcamento/{token}?ok=1", status_code=303)

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
