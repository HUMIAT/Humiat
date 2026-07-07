
import os
import secrets
import hashlib
import hmac
from datetime import date, datetime
from typing import Optional, List, Dict

from fastapi import FastAPI, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import Column, Integer, String, Text, ForeignKey, DateTime, Date, func, inspect, text, or_
from sqlalchemy.orm import Session, relationship, selectinload

from config import CHAVE_SESSAO, ORGANIZA_VERSAO, ADMIN_NOME, ADMIN_SENHA
from database import Base, SessionLocal, engine, get_db


app = FastAPI(title="HUMIAT", version=ORGANIZA_VERSAO)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

STATUS_VALIDOS = ["Pendente", "Aguardando Cliente", "Compras", "Financeiro", "Externo", "Cancelado", "Encerrado"]
DEPARTAMENTOS_CHAMADO = ["Cliente", "Compras", "Financeiro", "Externo"]
STATUS_CHAMADO = ["Aberto", "Em andamento", "Pedido realizado", "Previsão de entrega", "Produto recebido", "Concluído"]
MESES_PT = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"]
DIAS_PT = ["Segunda-feira", "Terça-feira", "Quarta-feira", "Quinta-feira", "Sexta-feira", "Sábado", "Domingo"]
RESPONSAVEIS = ["Junior", "Debora", "Luiz"]
DIAS_SEMANA = []  # legado interno: não aparece mais na tela


class Usuario(Base):
    __tablename__ = "usuarios"
    id = Column(Integer, primary_key=True)
    nome = Column(String(80), unique=True, nullable=False)
    telefone = Column(String(20), nullable=True)
    email = Column(String(140), nullable=True)
    cargo = Column(String(80), nullable=True)
    senha_hash = Column(String(255), nullable=False)
    is_admin = Column(Integer, nullable=False, default=0)
    pode_criar_tarefa = Column(Integer, nullable=False, default=1)
    pode_criar_projeto = Column(Integer, nullable=False, default=0)
    pode_criar_usuario = Column(Integer, nullable=False, default=0)
    pode_criar_etapa = Column(Integer, nullable=False, default=0)
    pode_cadastrar_cliente = Column(Integer, nullable=False, default=1)
    pode_cadastrar_item = Column(Integer, nullable=False, default=0)
    pode_acessar_compras = Column(Integer, nullable=False, default=0)
    pode_acessar_financeiro = Column(Integer, nullable=False, default=0)
    pode_cadastrar_banco = Column(Integer, nullable=False, default=0)
    pode_acessar_externo = Column(Integer, nullable=False, default=0)
    ativo = Column(Integer, nullable=False, default=1)
    departamentos = Column(String(200), nullable=True)
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
    bairro = Column(String(120), nullable=True)
    endereco = Column(String(255), nullable=True)
    email = Column(String(140), nullable=True)
    pacote = Column(String(30), nullable=True)
    falta_pacote = Column(Integer, nullable=True)
    plano = Column(String(60), nullable=True)
    observacao = Column(Text, nullable=True)
    criado_em = Column(DateTime, server_default=func.now())
    equipamentos = relationship("Equipamento", back_populates="cliente", cascade="all, delete-orphan")
    tarefas = relationship("Tarefa", back_populates="cliente")


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
    pago = Column(String(30), nullable=True)
    falta = Column(String(30), nullable=True)
    data_compra = Column(Date, nullable=True)
    previsao_entrega = Column(Date, nullable=True)
    maquina = Column(String(120), nullable=True)
    rede_instalada = Column(String(120), nullable=True)
    anydesk = Column(String(120), nullable=True)
    status = Column(String(30), nullable=False, default="Ativo")
    observacao = Column(Text, nullable=True)
    criado_em = Column(DateTime, server_default=func.now())
    cliente = relationship("Cliente", back_populates="equipamentos")


class Externo(Base):
    __tablename__ = "externos"
    id = Column(Integer, primary_key=True)
    nome = Column(String(140), nullable=False)
    telefone = Column(String(20), unique=True, nullable=False)
    empresa = Column(String(140), nullable=True)
    observacao = Column(Text, nullable=True)
    criado_em = Column(DateTime, server_default=func.now())


class AgendaEvento(Base):
    __tablename__ = "agenda_eventos"
    id = Column(Integer, primary_key=True)
    titulo = Column(String(180), nullable=False)
    data_evento = Column(Date, nullable=False)
    hora_evento = Column(String(5), nullable=True)
    tarefa_id = Column(Integer, ForeignKey("tarefas.id"), nullable=True)
    observacao = Column(Text, nullable=True)
    concluido = Column(Integer, nullable=False, default=0)
    criado_em = Column(DateTime, server_default=func.now())
    tarefa = relationship("Tarefa")


def limpar_telefone(valor: str) -> str:
    return "".join(ch for ch in (valor or "") if ch.isdigit())


def telefone_valido(valor: str) -> bool:
    numero = limpar_telefone(valor)
    return len(numero) in (10, 11) and numero[:2] != "00"


def formatar_telefone(valor: str) -> str:
    numero = limpar_telefone(valor)
    if len(numero) == 11:
        return f"({numero[:2]}) {numero[2:7]}-{numero[7:]}"
    if len(numero) == 10:
        return f"({numero[:2]}) {numero[2:6]}-{numero[6:]}"
    return valor or ""


templates.env.filters["telefone"] = formatar_telefone


def formatar_data_br(valor):
    if not valor:
        return "-"
    if isinstance(valor, datetime):
        return valor.strftime("%d/%m/%Y")
    return valor.strftime("%d/%m/%Y")


templates.env.filters["data_br"] = formatar_data_br


def formatar_moeda(valor):
    if valor in (None, ""):
        return "R$ 0,00"
    try:
        numero = float(str(valor).replace("R$", "").replace(".", "").replace(",", "."))
        return f"R$ {numero:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return str(valor)


templates.env.filters["moeda"] = formatar_moeda


class Projeto(Base):
    __tablename__ = "projetos"
    id = Column(Integer, primary_key=True)
    nome = Column(String(140), nullable=False)
    observacao = Column(Text, nullable=True)
    criado_em = Column(DateTime, server_default=func.now())
    missoes = relationship("Missao", back_populates="projeto", cascade="all, delete-orphan")


class Missao(Base):
    """
    Mantive o nome interno Missao para não quebrar banco antigo.
    Na tela, este cadastro aparece como Departamento / Etapa.
    """
    __tablename__ = "missoes"
    id = Column(Integer, primary_key=True)
    projeto_id = Column(Integer, ForeignKey("projetos.id"), nullable=False)
    nome = Column(String(140), nullable=False)
    observacao = Column(Text, nullable=True)
    finalidade = Column(String(30), nullable=False, default="interna")
    criado_em = Column(DateTime, server_default=func.now())
    projeto = relationship("Projeto", back_populates="missoes")
    tarefas = relationship("Tarefa", back_populates="missao", cascade="all, delete-orphan")


class Tarefa(Base):
    __tablename__ = "tarefas"
    id = Column(Integer, primary_key=True)
    projeto_id = Column(Integer, ForeignKey("projetos.id"), nullable=False)
    missao_id = Column(Integer, ForeignKey("missoes.id"), nullable=False)
    descricao = Column(Text, nullable=False)  # Na tela este campo aparece como Tarefa.
    responsavel = Column(String(80), nullable=False)
    status = Column(String(30), nullable=False, default="Pendente")
    ordem = Column(Integer, nullable=False, default=0)
    # Campos mantidos apenas para compatibilidade com bancos antigos.
    # Não aparecem mais nas telas e não participam do fluxo atual.
    tipo = Column(String(20), nullable=False, default="unica")
    dias_semana = Column(String(30), nullable=True)
    dependencia_id = Column(Integer, ForeignKey("tarefas.id"), nullable=True)
    observacao = Column(Text, nullable=True)  # Na tela aparece como Descrição opcional.
    data_tarefa = Column(Date, default=date.today)
    cliente_id = Column(Integer, ForeignKey("clientes.id"), nullable=True)
    criado_em = Column(DateTime, server_default=func.now())
    projeto = relationship("Projeto")
    cliente = relationship("Cliente", back_populates="tarefas")
    missao = relationship("Missao", back_populates="tarefas", foreign_keys=[missao_id])
    dependencia = relationship("Tarefa", remote_side=[id])
    chamados = relationship("Chamado", back_populates="tarefa", cascade="all, delete-orphan", foreign_keys="Chamado.tarefa_id")
    historicos = relationship("Historico", back_populates="tarefa", cascade="all, delete-orphan", order_by="Historico.criado_em.desc()")
    anexos = relationship("Anexo", back_populates="tarefa", cascade="all, delete-orphan")
    mensagens = relationship("Mensagem", back_populates="tarefa", cascade="all, delete-orphan")
    manutencao = relationship("Manutencao", back_populates="tarefa", uselist=False, cascade="all, delete-orphan")




class Historico(Base):
    __tablename__ = "historicos"
    id = Column(Integer, primary_key=True)
    tarefa_id = Column(Integer, ForeignKey("tarefas.id"), nullable=False)
    tipo = Column(String(40), nullable=False, default="evento")
    titulo = Column(String(160), nullable=False)
    descricao = Column(Text, nullable=True)
    usuario = Column(String(80), nullable=True)
    origem = Column(String(40), nullable=True)
    criado_em = Column(DateTime, server_default=func.now())
    tarefa = relationship("Tarefa", back_populates="historicos")


class Anexo(Base):
    __tablename__ = "anexos"
    id = Column(Integer, primary_key=True)
    tarefa_id = Column(Integer, ForeignKey("tarefas.id"), nullable=False)
    nome_arquivo = Column(String(255), nullable=False)
    caminho = Column(String(500), nullable=False)
    tipo = Column(String(80), nullable=True)
    observacao = Column(Text, nullable=True)
    criado_por = Column(String(80), nullable=True)
    criado_em = Column(DateTime, server_default=func.now())
    tarefa = relationship("Tarefa", back_populates="anexos")


class Mensagem(Base):
    __tablename__ = "mensagens"
    id = Column(Integer, primary_key=True)
    tarefa_id = Column(Integer, ForeignKey("tarefas.id"), nullable=True)
    cliente_id = Column(Integer, ForeignKey("clientes.id"), nullable=True)
    canal = Column(String(30), nullable=False, default="WhatsApp")
    direcao = Column(String(20), nullable=False, default="saida")
    texto = Column(Text, nullable=False)
    status = Column(String(30), nullable=False, default="rascunho")
    enviado_por = Column(String(80), nullable=True)
    enviado_em = Column(DateTime, nullable=True)
    criado_em = Column(DateTime, server_default=func.now())
    tarefa = relationship("Tarefa", back_populates="mensagens")
    cliente = relationship("Cliente")

class Chamado(Base):
    __tablename__ = "chamados"
    id = Column(Integer, primary_key=True)
    tarefa_id = Column(Integer, ForeignKey("tarefas.id"), nullable=False)
    departamento = Column(String(30), nullable=False)
    descricao = Column(Text, nullable=False)
    status = Column(String(30), nullable=False, default="Aberto")
    criado_por = Column(String(80), nullable=True)
    finalizado_por = Column(String(80), nullable=True)
    fornecedor = Column(String(140), nullable=True)
    valor = Column(String(40), nullable=True)
    previsao_entrega = Column(Date, nullable=True)
    data_recebimento = Column(Date, nullable=True)
    externo_id = Column(Integer, ForeignKey("externos.id"), nullable=True)
    data_envio = Column(Date, nullable=True)
    data_devolucao = Column(Date, nullable=True)
    observacao = Column(Text, nullable=True)
    criado_em = Column(DateTime, server_default=func.now())
    concluido_em = Column(DateTime, nullable=True)
    tarefa = relationship("Tarefa", back_populates="chamados", foreign_keys=[tarefa_id])
    externo = relationship("Externo")




# =========================
# ORGANIZA 7.1 - módulos internos
# =========================

STATUS_MANUTENCAO = [
    "Recebida",
    "Orçamento Cliente",
    "Pendente Financeiro",
    "Pendente Compras",
    "Pendente Externo",
    "Aguardando Financeiro",
    "Aguardando Cliente Buscar",
    "Pendente",
    "Aguardando Peça",
    "Finalizada",
    "Entregue",
    "Cancelada",
]

STATUS_COMPRA = ["Solicitada", "Comprada", "Aguardando Recebimento", "Recebida", "Cancelada"]
STATUS_CONTA_RECEBER = ["A receber", "Recebido", "Cancelado"]
STATUS_CONTA_PAGAR = ["Pendente", "Pago", "Cancelado"]
TIPOS_CONTA = ["receber", "pagar"]


class Manutencao(Base):
    __tablename__ = "manutencoes"
    id = Column(Integer, primary_key=True)
    tarefa_id = Column(Integer, ForeignKey("tarefas.id"), nullable=True, unique=True)
    cliente_id = Column(Integer, ForeignKey("clientes.id"), nullable=True)
    nome_cliente = Column(String(140), nullable=False)
    telefone = Column(String(20), nullable=True)
    equipamento = Column(String(140), nullable=True)
    problema = Column(Text, nullable=False)
    status = Column(String(40), nullable=False, default="Recebida")
    responsavel = Column(String(80), nullable=True)
    valor_orcamento = Column(String(40), nullable=True)
    forma_pagamento = Column(Text, nullable=True)
    data_entrada = Column(Date, default=date.today)
    data_orcamento = Column(Date, nullable=True)
    hora_orcamento = Column(String(10), nullable=True)
    prazo_dias = Column(String(10), nullable=True, default="20")
    data_conclusao = Column(Date, nullable=True)
    data_retirada = Column(Date, nullable=True)
    observacao = Column(Text, nullable=True)
    criado_em = Column(DateTime, server_default=func.now())
    atualizado_em = Column(DateTime, server_default=func.now(), onupdate=func.now())

    tarefa = relationship("Tarefa", back_populates="manutencao")
    cliente = relationship("Cliente")
    compras = relationship("Compra", back_populates="manutencao")
    itens = relationship("ManutencaoItem", back_populates="manutencao", cascade="all, delete-orphan")


class ProdutoServico(Base):
    __tablename__ = "produtos_servicos"
    id = Column(Integer, primary_key=True)
    nome = Column(String(140), nullable=False)
    categoria = Column(String(80), nullable=True)
    tipo = Column(String(30), nullable=False, default="Produto")  # Produto ou Serviço
    valor_padrao = Column(String(40), nullable=True)
    ativo = Column(Integer, nullable=False, default=1)
    observacao = Column(Text, nullable=True)
    criado_em = Column(DateTime, server_default=func.now())


class ManutencaoItem(Base):
    __tablename__ = "manutencao_itens"
    id = Column(Integer, primary_key=True)
    manutencao_id = Column(Integer, ForeignKey("manutencoes.id"), nullable=False)
    produto_servico_id = Column(Integer, ForeignKey("produtos_servicos.id"), nullable=True)
    nome = Column(String(140), nullable=False)
    tipo = Column(String(30), nullable=False, default="Produto")
    quantidade = Column(String(40), nullable=True)
    valor_unitario = Column(String(40), nullable=True)
    valor_total = Column(String(40), nullable=True)
    aprovado = Column(Integer, nullable=False, default=0)
    comprar = Column(Integer, nullable=False, default=0)
    externo = Column(Integer, nullable=False, default=0)
    observacao = Column(Text, nullable=True)
    criado_em = Column(DateTime, server_default=func.now())

    manutencao = relationship("Manutencao", back_populates="itens")
    produto_servico = relationship("ProdutoServico")
class Compra(Base):
    __tablename__ = "compras"
    id = Column(Integer, primary_key=True)
    manutencao_id = Column(Integer, ForeignKey("manutencoes.id"), nullable=True)
    produto = Column(String(140), nullable=False)
    quantidade = Column(String(40), nullable=True)
    fornecedor = Column(String(140), nullable=True)
    valor = Column(String(40), nullable=True)
    status = Column(String(40), nullable=False, default="Solicitada")
    previsao_entrega = Column(Date, nullable=True)
    data_recebimento = Column(Date, nullable=True)
    responsavel = Column(String(80), nullable=True)
    observacao = Column(Text, nullable=True)
    criado_em = Column(DateTime, server_default=func.now())
    manutencao = relationship("Manutencao", back_populates="compras")


class ContaFinanceira(Base):
    __tablename__ = "financeiro_contas"
    id = Column(Integer, primary_key=True)
    tipo = Column(String(20), nullable=False)  # receber ou pagar
    origem = Column(String(60), nullable=True)
    origem_id = Column(Integer, nullable=True)
    pessoa = Column(String(140), nullable=False)
    categoria = Column(String(80), nullable=True)
    descricao = Column(Text, nullable=False)
    valor = Column(String(40), nullable=False)
    vencimento = Column(Date, nullable=True)
    banco = Column(String(80), nullable=True)
    forma = Column(String(80), nullable=True)
    status = Column(String(40), nullable=False, default="Pendente")
    pago_em = Column(Date, nullable=True)
    observacao = Column(Text, nullable=True)
    criado_em = Column(DateTime, server_default=func.now())
    movimentos = relationship("BancoMovimento", back_populates="conta", cascade="all, delete-orphan")



class BancoConta(Base):
    __tablename__ = "bancos"
    id = Column(Integer, primary_key=True)
    nome = Column(String(80), unique=True, nullable=False)
    saldo_inicial = Column(String(40), nullable=True)
    ativo = Column(Integer, nullable=False, default=1)
    criado_em = Column(DateTime, server_default=func.now())


class BancoMovimento(Base):
    __tablename__ = "banco_movimentos"
    id = Column(Integer, primary_key=True)
    conta_id = Column(Integer, ForeignKey("financeiro_contas.id"), nullable=True)
    tipo = Column(String(20), nullable=False)  # entrada ou saida
    banco = Column(String(80), nullable=False)
    categoria = Column(String(80), nullable=True)
    descricao = Column(Text, nullable=False)
    valor = Column(String(40), nullable=False)
    data = Column(Date, default=date.today)
    conciliado = Column(Integer, nullable=False, default=1)
    criado_em = Column(DateTime, server_default=func.now())
    conta = relationship("ContaFinanceira", back_populates="movimentos")


def gerar_hash_senha(senha: str, salt: Optional[str] = None) -> str:
    salt = salt or secrets.token_hex(16)
    chave = hashlib.pbkdf2_hmac("sha256", senha.encode("utf-8"), salt.encode("utf-8"), 120_000)
    return f"{salt}${chave.hex()}"


def verificar_senha(senha: str, senha_hash: str) -> bool:
    try:
        salt, hash_salvo = senha_hash.split("$", 1)
    except ValueError:
        return False
    hash_digitado = gerar_hash_senha(senha, salt).split("$", 1)[1]
    return hmac.compare_digest(hash_digitado, hash_salvo)


def criar_assinatura(valor: str) -> str:
    return hmac.new(CHAVE_SESSAO.encode("utf-8"), valor.encode("utf-8"), hashlib.sha256).hexdigest()


def criar_cookie_sessao(nome_usuario: str) -> str:
    return f"{nome_usuario}|{criar_assinatura(nome_usuario)}"


def ler_usuario_cookie(request: Request) -> Optional[str]:
    cookie = request.cookies.get("organiza_sessao")
    if not cookie or "|" not in cookie:
        return None
    nome, assinatura = cookie.rsplit("|", 1)
    if hmac.compare_digest(assinatura, criar_assinatura(nome)):
        return nome
    return None


def usuario_logado(request: Request, db: Session = Depends(get_db)) -> Usuario:
    nome = ler_usuario_cookie(request)
    if not nome:
        raise HTTPException(status_code=307, headers={"Location": "/area-restrita/login"})
    usuario = db.query(Usuario).filter(Usuario.nome == nome).first()
    if not usuario or not usuario.ativo:
        raise HTTPException(status_code=307, headers={"Location": "/area-restrita/login"})
    return usuario


def exigir_admin(usuario: Usuario):
    if not usuario.is_admin:
        raise HTTPException(status_code=307, headers={"Location": "/organiza/acesso-negado"})


def tem_permissao(usuario: Usuario, permissao: str) -> bool:
    return bool(usuario and (usuario.is_admin or getattr(usuario, permissao, 0)))


def exigir_permissao(usuario: Usuario, permissao: str):
    if tem_permissao(usuario, permissao):
        return
    raise HTTPException(status_code=307, headers={"Location": "/organiza/acesso-negado"})





def data_hora_atual():
    agora = datetime.now()
    return {
        "data_extenso": f"{DIAS_PT[agora.weekday()]}, {agora.day:02d} de {MESES_PT[agora.month - 1]} de {agora.year}",
        "hora": agora.strftime("%H:%M"),
    }

templates.env.globals["data_hora_atual"] = data_hora_atual
templates.env.globals["tem_permissao"] = tem_permissao

def ordenar_tarefas_query(query):
    return query.order_by(Tarefa.ordem.asc(), Tarefa.criado_em.asc(), Tarefa.id.asc())


def normalizar_ordem_tarefas(db: Session, responsavel: Optional[str] = None, missao_id: Optional[int] = None):
    query = db.query(Tarefa)
    if responsavel:
        query = query.filter(Tarefa.responsavel == responsavel)
    if missao_id:
        query = query.filter(Tarefa.missao_id == missao_id)
    tarefas = query.order_by(Tarefa.ordem.asc(), Tarefa.criado_em.asc(), Tarefa.id.asc()).all()
    for indice, tarefa in enumerate(tarefas, start=1):
        tarefa.ordem = indice
    db.flush()


def trocar_ordem_tarefa(db: Session, tarefa: Tarefa, direcao: str):
    normalizar_ordem_tarefas(db, responsavel=tarefa.responsavel)
    tarefas = db.query(Tarefa).filter(Tarefa.responsavel == tarefa.responsavel).order_by(Tarefa.ordem.asc(), Tarefa.criado_em.asc(), Tarefa.id.asc()).all()
    pos = next((i for i, item in enumerate(tarefas) if item.id == tarefa.id), None)
    if pos is None:
        return
    if direcao == "subir" and pos > 0:
        tarefas[pos].ordem, tarefas[pos - 1].ordem = tarefas[pos - 1].ordem, tarefas[pos].ordem
    elif direcao == "descer" and pos < len(tarefas) - 1:
        tarefas[pos].ordem, tarefas[pos + 1].ordem = tarefas[pos + 1].ordem, tarefas[pos].ordem
    elif direcao == "topo":
        alvo = tarefas.pop(pos)
        tarefas.insert(0, alvo)
        for indice, item in enumerate(tarefas, start=1):
            item.ordem = indice
    elif direcao == "final":
        alvo = tarefas.pop(pos)
        tarefas.append(alvo)
        for indice, item in enumerate(tarefas, start=1):
            item.ordem = indice



def responsaveis_db(db: Session) -> List[str]:
    nomes = [u.nome for u in db.query(Usuario).filter(Usuario.ativo == 1).order_by(Usuario.nome).all()]
    return nomes or RESPONSAVEIS


def tarefa_aparece_hoje(tarefa: Tarefa) -> bool:
    # O dashboard mostra somente o que ainda exige ação.
    # OS encerrada continua disponível nas consultas/histórico, mas sai do painel.
    if tarefa.status in {"Encerrado", "Entregue"}:
        return False
    return True


def calcular_percentual_projeto(projeto: Projeto) -> int:
    tarefas = [t for etapa in projeto.missoes for t in etapa.tarefas]
    if not tarefas:
        return 0
    entregues = len([t for t in tarefas if t.status == "Entregue"])
    return round((entregues / len(tarefas)) * 100)


def agrupar_por_projeto(tarefas: List[Tarefa]) -> Dict[str, List[Tarefa]]:
    grupos: Dict[str, List[Tarefa]] = {}
    for tarefa in tarefas:
        grupos.setdefault(tarefa.projeto.nome, []).append(tarefa)
    return grupos


def departamentos_usuario(usuario: Usuario) -> List[str]:
    return [d.strip() for d in (usuario.departamentos or "").split(",") if d.strip()]


def usuario_tem_departamento(usuario: Usuario, departamento: str) -> bool:
    return bool(usuario.is_admin or departamento in departamentos_usuario(usuario))


STATUS_DEPARTAMENTO_CHAMADO = {
    "Aguardando Cliente": "Cliente",
    "Compras": "Compras",
    "Financeiro": "Financeiro",
    "Externo": "Externo",
}


def status_aguardando_departamento(departamento: str) -> str:
    if departamento in {"Compras", "Financeiro", "Externo"}:
        return departamento
    return f"Aguardando {departamento}"


def departamento_por_status(status: str) -> Optional[str]:
    return STATUS_DEPARTAMENTO_CHAMADO.get(status)


def validar_acao_para_chamado(status: str, acao_chamado: str):
    departamento = departamento_por_status(status)
    if departamento and not (acao_chamado or "").strip():
        raise HTTPException(
            status_code=400,
            detail=f"Para enviar a tarefa para {status}, preencha a Ação do chamado de {departamento}."
        )


def criar_chamado_se_necessario(db: Session, tarefa: Tarefa, status_anterior: Optional[str], usuario: Usuario, acao_chamado: str = ""):
    departamento = departamento_por_status(tarefa.status)
    if not departamento or tarefa.status == status_anterior:
        return

    descricao_chamado = (acao_chamado or "").strip()
    if not descricao_chamado:
        raise HTTPException(
            status_code=400,
            detail=f"A ação do chamado de {departamento} é obrigatória."
        )

    aberto = db.query(Chamado).filter(
        Chamado.tarefa_id == tarefa.id,
        Chamado.departamento == departamento,
        Chamado.status != "Concluído"
    ).first()
    if aberto:
        return
    db.add(Chamado(
        tarefa_id=tarefa.id,
        departamento=departamento,
        descricao=descricao_chamado,
        criado_por=usuario.nome,
        status="Aberto"
    ))
    registrar_historico(db, tarefa.id, f"Chamado aberto para {departamento}", descricao_chamado, usuario, tipo="chamado")



def registrar_historico(db: Session, tarefa_id: int, titulo: str, descricao: str = "", usuario: Optional[Usuario] = None, tipo: str = "evento", origem: str = "Organiza"):
    db.add(Historico(
        tarefa_id=tarefa_id,
        tipo=tipo,
        titulo=titulo.strip(),
        descricao=(descricao or "").strip() or None,
        usuario=usuario.nome if usuario else None,
        origem=origem,
    ))


def status_tarefa_aberta(status: str) -> bool:
    return status in {"Criado","Financeiro","Compras","Externo"}

def parse_data(valor: str):
    valor = (valor or "").strip()
    if not valor:
        return None
    return datetime.strptime(valor, "%Y-%m-%d").date()


@app.on_event("startup")
def iniciar_banco():
    Base.metadata.create_all(bind=engine)
    # Atualização simples para bancos SQLite já existentes.
    insp = inspect(engine)
    with engine.begin() as conn:
        if "missoes" in insp.get_table_names():
            colunas_missoes = [c["name"] for c in insp.get_columns("missoes")]
            if "finalidade" not in colunas_missoes:
                conn.execute(text("ALTER TABLE missoes ADD COLUMN finalidade VARCHAR(30) NOT NULL DEFAULT 'interna'"))
        if "usuarios" in insp.get_table_names():
            colunas_usuarios = [c["name"] for c in insp.get_columns("usuarios")]
            novas_colunas = {
                "telefone": "VARCHAR(20)",
                "email": "VARCHAR(140)",
                "cargo": "VARCHAR(80)",
                "is_admin": "INTEGER NOT NULL DEFAULT 0",
                "pode_criar_tarefa": "INTEGER NOT NULL DEFAULT 1",
                "pode_criar_projeto": "INTEGER NOT NULL DEFAULT 0",
                "pode_criar_usuario": "INTEGER NOT NULL DEFAULT 0",
                "pode_criar_etapa": "INTEGER NOT NULL DEFAULT 0",
                "pode_cadastrar_cliente": "INTEGER NOT NULL DEFAULT 1",
                "pode_cadastrar_item": "INTEGER NOT NULL DEFAULT 0",
                "pode_acessar_compras": "INTEGER NOT NULL DEFAULT 0",
                "pode_acessar_financeiro": "INTEGER NOT NULL DEFAULT 0",
                "pode_cadastrar_banco": "INTEGER NOT NULL DEFAULT 0",
                "pode_acessar_externo": "INTEGER NOT NULL DEFAULT 0",
                "ativo": "INTEGER NOT NULL DEFAULT 1",
                "departamentos": "VARCHAR(200)",
            }
            for coluna, tipo_sql in novas_colunas.items():
                if coluna not in colunas_usuarios:
                    conn.execute(text(f"ALTER TABLE usuarios ADD COLUMN {coluna} {tipo_sql}"))
        if "clientes" in insp.get_table_names():
            colunas_clientes = [c["name"] for c in insp.get_columns("clientes")]
            novas_colunas_clientes = {
                "documento": "VARCHAR(30)",
                "cep": "VARCHAR(20)",
                "cidade": "VARCHAR(120)",
                "bairro": "VARCHAR(120)",
                "endereco": "VARCHAR(255)",
                "email": "VARCHAR(140)",
                "pacote": "VARCHAR(30)",
                "falta_pacote": "INTEGER",
                "plano": "VARCHAR(60)",
            }
            for coluna, tipo_sql in novas_colunas_clientes.items():
                if coluna not in colunas_clientes:
                    conn.execute(text(f"ALTER TABLE clientes ADD COLUMN {coluna} {tipo_sql}"))
        if "tarefas" in insp.get_table_names():
            colunas_tarefas = [c["name"] for c in insp.get_columns("tarefas")]
            if "cliente_id" not in colunas_tarefas:
                conn.execute(text("ALTER TABLE tarefas ADD COLUMN cliente_id INTEGER"))
            if "data_tarefa" not in colunas_tarefas:
                conn.execute(text("ALTER TABLE tarefas ADD COLUMN data_tarefa DATE"))
            if "ordem" not in colunas_tarefas:
                conn.execute(text("ALTER TABLE tarefas ADD COLUMN ordem INTEGER NOT NULL DEFAULT 0"))
            # Organiza 8.1: painel usa somente cards de responsabilidade.
            conn.execute(text("UPDATE tarefas SET status = 'Pendente' WHERE status IN ('Aberta', 'Para Fazer', 'Em andamento', 'Aguardando', 'Aguardando Cliente Orçamento', 'Enviar Orçamento', 'Pendente Financeiro')"))
            conn.execute(text("UPDATE tarefas SET status = 'Aguardando Cliente' WHERE status IN ('Aguardando Cliente')"))
            conn.execute(text("UPDATE tarefas SET status = 'Compras' WHERE status IN ('Aguardando Compras', 'Pendente Compras')"))
            conn.execute(text("UPDATE tarefas SET status = 'Financeiro' WHERE status IN ('Aguardando Financeiro')"))
            conn.execute(text("UPDATE tarefas SET status = 'Externo' WHERE status IN ('Aguardando Externo', 'Pendente Externo')"))
            conn.execute(text("UPDATE tarefas SET status = 'Encerrado' WHERE status IN ('Concluída', 'Concluida', 'Pronta', 'Concluído', 'Entregue')"))
            conn.execute(text("UPDATE tarefas SET status = 'Cancelado' WHERE status IN ('Cancelada', 'Cancelado')"))
            conn.execute(text("UPDATE tarefas SET status = 'Pendente' WHERE status IS NULL OR status NOT IN ('Criado','Financeiro','Compras','Externo','Concluído','Entregue','Cancelado')"))
        if "manutencoes" in insp.get_table_names():
            colunas_manutencoes = [c["name"] for c in insp.get_columns("manutencoes")]
            if "tarefa_id" not in colunas_manutencoes:
                conn.execute(text("ALTER TABLE manutencoes ADD COLUMN tarefa_id INTEGER"))
            if "data_orcamento" not in colunas_manutencoes:
                conn.execute(text("ALTER TABLE manutencoes ADD COLUMN data_orcamento DATE"))
            if "hora_orcamento" not in colunas_manutencoes:
                conn.execute(text("ALTER TABLE manutencoes ADD COLUMN hora_orcamento VARCHAR(10)"))
            if "prazo_dias" not in colunas_manutencoes:
                conn.execute(text("ALTER TABLE manutencoes ADD COLUMN prazo_dias VARCHAR(10) DEFAULT '20'"))
        if "manutencao_itens" in insp.get_table_names():
            colunas_itens_manutencao = [c["name"] for c in insp.get_columns("manutencao_itens")]
            if "comprar" not in colunas_itens_manutencao:
                conn.execute(text("ALTER TABLE manutencao_itens ADD COLUMN comprar INTEGER NOT NULL DEFAULT 0"))
            if "externo" not in colunas_itens_manutencao:
                conn.execute(text("ALTER TABLE manutencao_itens ADD COLUMN externo INTEGER NOT NULL DEFAULT 0"))
            conn.execute(text("UPDATE manutencao_itens SET aprovado = 0 WHERE aprovado IS NULL"))
    db = SessionLocal()
    try:
        # Banco novo em produção:
        # cria somente 1 administrador inicial.
        # Depois, os demais usuários devem ser criados pela tela de Usuários.
        admin_nome = ADMIN_NOME
        admin_senha = ADMIN_SENHA

        if db.query(BancoConta).count() == 0:
            db.add(BancoConta(nome="Santander", saldo_inicial="0,00", ativo=1))

        total_usuarios = db.query(Usuario).count()
        if total_usuarios == 0:
            db.add(Usuario(
                nome=admin_nome,
                senha_hash=gerar_hash_senha(admin_senha),
                cargo="Administrador",
                is_admin=1,
                pode_criar_tarefa=1,
                pode_criar_projeto=1,
                pode_criar_usuario=1,
                pode_criar_etapa=1,
                ativo=1,
                departamentos=",".join(DEPARTAMENTOS_CHAMADO),
            ))
        else:
            admin = db.query(Usuario).filter(Usuario.nome == admin_nome).first()
            if admin:
                admin.is_admin = 1
                admin.pode_criar_tarefa = 1
                admin.pode_criar_projeto = 1
                admin.pode_criar_usuario = 1
                admin.pode_criar_etapa = 1
                admin.ativo = 1

        # Garante que toda tarefa existente tenha a primeira linha da timeline.
        tarefas_sem_historico = (
            db.query(Tarefa)
            .outerjoin(Historico, Historico.tarefa_id == Tarefa.id)
            .filter(Historico.id.is_(None))
            .all()
        )
        for tarefa in tarefas_sem_historico:
            db.add(Historico(
                tarefa_id=tarefa.id,
                tipo="tarefa",
                titulo="Criado em",
                descricao=tarefa.descricao,
                usuario=tarefa.responsavel,
                origem="Organiza",
                criado_em=tarefa.criado_em,
            ))

        db.commit()
    finally:
        db.close()


@app.get("/", response_class=HTMLResponse)
def pagina_inicial(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/saude")
def saude():
    return {"ok": True, "projeto": "HUMIAT", "modulo": "Organiza", "versao": ORGANIZA_VERSAO}


@app.get("/area-restrita/login", response_class=HTMLResponse)
def login(request: Request, erro: str = ""):
    return templates.TemplateResponse("organiza/login.html", {"request": request, "erro": erro})


@app.post("/area-restrita/login")
def entrar(usuario: str = Form(...), senha: str = Form(...), db: Session = Depends(get_db)):
    encontrado = db.query(Usuario).filter(Usuario.nome == usuario.strip()).first()
    if not encontrado or not verificar_senha(senha, encontrado.senha_hash):
        return RedirectResponse("/area-restrita/login?erro=Usuário ou senha inválidos", status_code=303)
    resposta = RedirectResponse("/organiza", status_code=303)
    resposta.set_cookie("organiza_sessao", criar_cookie_sessao(encontrado.nome), httponly=True, samesite="lax")
    return resposta


@app.get("/area-restrita/sair")
def sair():
    resposta = RedirectResponse("/area-restrita/login", status_code=303)
    resposta.delete_cookie("organiza_sessao")
    return resposta


@app.get("/organiza/acesso-negado", response_class=HTMLResponse)
def acesso_negado(request: Request, usuario: Usuario = Depends(usuario_logado)):
    return templates.TemplateResponse("organiza/acesso_negado.html", {"request": request, "usuario": usuario})


@app.get("/organiza", response_class=HTMLResponse)
def painel(request: Request, responsavel: Optional[str] = None, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    tarefas = db.query(Tarefa).order_by(Tarefa.ordem.asc(), Tarefa.criado_em.asc(), Tarefa.id.asc()).all()
    lista_responsaveis = responsaveis_db(db)
    responsavel_painel = responsavel if responsavel in lista_responsaveis else usuario.nome

    tarefas_do_responsavel = [t for t in tarefas if t.responsavel == responsavel_painel and tarefa_aparece_hoje(t)]
    tarefas_hoje_usuario = [t for t in tarefas_do_responsavel if t.status == "Pendente"]
    tarefas_canceladas = [t for t in tarefas_do_responsavel if t.status == "Cancelado"]
    aguardando_cliente = [t for t in tarefas_do_responsavel if t.status == "Aguardando Cliente"]
    aguardando_compras = [t for t in tarefas_do_responsavel if t.status == "Compras"]
    aguardando_financeiro = [t for t in tarefas_do_responsavel if t.status == "Financeiro"]
    aguardando_externo = [t for t in tarefas_do_responsavel if t.status == "Externo"]

    deps_usuario = departamentos_usuario(usuario)
    chamados_query = db.query(Chamado).filter(Chamado.status != "Concluído")
    if not usuario.is_admin:
        if deps_usuario:
            chamados_query = chamados_query.filter(Chamado.departamento.in_(deps_usuario))
        else:
            chamados_query = chamados_query.filter(Chamado.id == -1)
    chamados_visiveis = chamados_query.order_by(Chamado.criado_em.desc()).all()

    hoje = date.today()
    chamados_atrasados = [
        c for c in chamados_visiveis
        if (c.departamento == "Compras" and c.previsao_entrega and c.previsao_entrega < hoje and not c.data_recebimento)
        or (c.departamento == "Externo" and c.data_devolucao and c.data_devolucao < hoje)
    ]

    resumo = {status: len([t for t in tarefas if t.responsavel == responsavel_painel and t.status == status]) for status in STATUS_VALIDOS}
    resumo_chamados = {dep: len([c for c in chamados_visiveis if c.departamento == dep]) for dep in DEPARTAMENTOS_CHAMADO}
    por_responsavel = {nome: len([t for t in tarefas if t.responsavel == nome and tarefa_aparece_hoje(t)]) for nome in lista_responsaveis}
    projetos = db.query(Projeto).order_by(Projeto.criado_em.desc()).all()
    percentuais = {p.id: calcular_percentual_projeto(p) for p in projetos}

    agenda_eventos = (
        db.query(AgendaEvento)
        .filter(AgendaEvento.concluido == 0)
        .order_by(AgendaEvento.data_evento.asc(), AgendaEvento.hora_evento.asc(), AgendaEvento.id.asc())
        .limit(20)
        .all()
    )

    return templates.TemplateResponse("organiza/painel.html", {
        "request": request, "usuario": usuario, "responsavel_painel": responsavel_painel,
        "tarefas_hoje_usuario": tarefas_hoje_usuario, "tarefas_canceladas": tarefas_canceladas, "tarefas_do_responsavel": tarefas_do_responsavel,
        "aguardando_cliente": aguardando_cliente, "aguardando_compras": aguardando_compras,
        "aguardando_financeiro": aguardando_financeiro, "aguardando_externo": aguardando_externo,
        "chamados_visiveis": chamados_visiveis, "chamados_atrasados": chamados_atrasados,
        "resumo_chamados": resumo_chamados, "departamentos_usuario": deps_usuario,
        "resumo": resumo, "por_responsavel": por_responsavel, "projetos": projetos, "percentuais": percentuais,
        "agenda_eventos": agenda_eventos, "hoje": date.today(),
        "status_validos": STATUS_VALIDOS, "responsaveis": lista_responsaveis, "agora": data_hora_atual(),
    })

@app.get("/organiza/detalhes/{tipo}/{valor}", response_class=HTMLResponse)
def detalhes(tipo: str, valor: str, request: Request, responsavel: Optional[str] = None, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    tarefas = db.query(Tarefa).order_by(Tarefa.ordem.asc(), Tarefa.criado_em.asc(), Tarefa.id.asc()).all()
    titulo = "Tarefas"
    if tipo == "responsavel":
        tarefas = [t for t in tarefas if t.responsavel == valor and tarefa_aparece_hoje(t)]
        titulo = f"Tarefas de {valor}"
    elif tipo == "status":
        tarefas = [t for t in tarefas if t.status == valor]
        if responsavel in responsaveis_db(db):
            tarefas = [t for t in tarefas if t.responsavel == responsavel]
            titulo = f"{valor} de {responsavel}"
        else:
            titulo = f"Tarefas com status: {valor}"
    elif tipo == "hoje":
        tarefas = [t for t in tarefas if tarefa_aparece_hoje(t)]
        titulo = "Tarefas de hoje"
    else:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse("organiza/detalhes.html", {
        "request": request, "usuario": usuario, "titulo": titulo, "grupos": agrupar_por_projeto(tarefas),
        "status_validos": STATUS_VALIDOS, "tipo": tipo, "valor": valor
    })


@app.get("/organiza/projetos/novo", response_class=HTMLResponse)
def novo_projeto(request: Request, usuario: Usuario = Depends(usuario_logado)):
    exigir_permissao(usuario, "pode_criar_projeto")
    return templates.TemplateResponse("organiza/projeto_form.html", {"request": request, "usuario": usuario, "projeto": None})


@app.post("/organiza/projetos/novo")
def criar_projeto(nome: str = Form(...), observacao: str = Form(""), usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    exigir_permissao(usuario, "pode_criar_projeto")
    db.add(Projeto(nome=nome.strip(), observacao=observacao.strip() or None))
    db.commit()
    return RedirectResponse("/organiza/projetos", status_code=303)


@app.get("/organiza/projetos/{projeto_id}/editar", response_class=HTMLResponse)
def editar_projeto(projeto_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    exigir_permissao(usuario, "pode_criar_projeto")
    projeto = db.query(Projeto).filter(Projeto.id == projeto_id).first()
    if not projeto: raise HTTPException(status_code=404)
    return templates.TemplateResponse("organiza/projeto_form.html", {"request": request, "usuario": usuario, "projeto": projeto})


@app.post("/organiza/projetos/{projeto_id}/editar")
def salvar_projeto(projeto_id: int, nome: str = Form(...), observacao: str = Form(""), usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    exigir_permissao(usuario, "pode_criar_projeto")
    projeto = db.query(Projeto).filter(Projeto.id == projeto_id).first()
    if not projeto: raise HTTPException(status_code=404)
    projeto.nome = nome.strip()
    projeto.observacao = observacao.strip() or None
    db.commit()
    return RedirectResponse(f"/organiza/projetos/{projeto_id}", status_code=303)


@app.get("/organiza/projetos/{projeto_id}", response_class=HTMLResponse)
def ver_projeto(projeto_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    projeto = db.query(Projeto).filter(Projeto.id == projeto_id).first()
    if not projeto: raise HTTPException(status_code=404)
    tarefas = db.query(Tarefa).filter(Tarefa.projeto_id == projeto_id).order_by(Tarefa.ordem.asc(), Tarefa.criado_em.asc(), Tarefa.id.asc()).all()
    percentual = calcular_percentual_projeto(projeto)
    return templates.TemplateResponse("organiza/projeto.html", {
        "request": request, "usuario": usuario, "projeto": projeto, "tarefas": tarefas,
        "percentual": percentual, "status_validos": STATUS_VALIDOS
    })


@app.get("/organiza/projetos/{projeto_id}/missoes/nova", response_class=HTMLResponse)
def nova_missao(projeto_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    exigir_permissao(usuario, "pode_criar_etapa")
    projeto = db.query(Projeto).filter(Projeto.id == projeto_id).first()
    if not projeto: raise HTTPException(status_code=404)
    return templates.TemplateResponse("organiza/missao_form.html", {"request": request, "usuario": usuario, "projeto": projeto, "missao": None})


@app.post("/organiza/projetos/{projeto_id}/missoes/nova")
def criar_missao(projeto_id: int, nome: str = Form(...), finalidade: str = Form("interna"), observacao: str = Form(""), usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    exigir_permissao(usuario, "pode_criar_etapa")
    finalidade = finalidade if finalidade in ["interna", "cliente"] else "interna"
    db.add(Missao(projeto_id=projeto_id, nome=nome.strip(), finalidade=finalidade, observacao=observacao.strip() or None))
    db.commit()
    return RedirectResponse(f"/organiza/projetos/{projeto_id}", status_code=303)


@app.get("/organiza/projetos/{projeto_id}/missoes/{missao_id}/editar", response_class=HTMLResponse)
def editar_missao(projeto_id: int, missao_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    exigir_permissao(usuario, "pode_criar_etapa")
    projeto = db.query(Projeto).filter(Projeto.id == projeto_id).first()
    missao = db.query(Missao).filter(Missao.id == missao_id, Missao.projeto_id == projeto_id).first()
    if not projeto or not missao: raise HTTPException(status_code=404)
    return templates.TemplateResponse("organiza/missao_form.html", {"request": request, "usuario": usuario, "projeto": projeto, "missao": missao})


@app.post("/organiza/projetos/{projeto_id}/missoes/{missao_id}/editar")
def salvar_missao(projeto_id: int, missao_id: int, nome: str = Form(...), finalidade: str = Form("interna"), observacao: str = Form(""), usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    exigir_permissao(usuario, "pode_criar_etapa")
    missao = db.query(Missao).filter(Missao.id == missao_id, Missao.projeto_id == projeto_id).first()
    if not missao: raise HTTPException(status_code=404)
    missao.nome = nome.strip()
    missao.finalidade = finalidade if finalidade in ["interna", "cliente"] else "interna"
    missao.observacao = observacao.strip() or None
    db.commit()
    return RedirectResponse(f"/organiza/projetos/{projeto_id}", status_code=303)


@app.get("/organiza/tarefas/nova", response_class=HTMLResponse)
def nova_tarefa(request: Request, projeto_id: Optional[int] = None, missao_id: Optional[int] = None, dependencia_id: Optional[int] = None, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    exigir_permissao(usuario, "pode_criar_tarefa")
    projetos = db.query(Projeto).order_by(Projeto.nome).all()
    missoes = db.query(Missao).order_by(Missao.nome).all()
    tarefas = db.query(Tarefa).order_by(Tarefa.ordem.asc(), Tarefa.criado_em.asc(), Tarefa.id.asc()).all()
    clientes = db.query(Cliente).order_by(Cliente.nome).all()
    return templates.TemplateResponse("organiza/tarefa_form.html", {
        "request": request, "usuario": usuario, "projetos": projetos, "missoes": missoes, "tarefa": None,
        "tarefas": tarefas, "responsaveis": responsaveis_db(db), "status_validos": STATUS_VALIDOS,
        "projeto_id": projeto_id, "missao_id": missao_id, "dependencia_id": dependencia_id, "clientes": clientes, "hoje": date.today()
    })


@app.post("/organiza/tarefas/nova")
def criar_tarefa(projeto_id: int = Form(...), missao_id: int = Form(...), descricao: str = Form(""), responsavel: str = Form(...), status: str = Form("Pendente"), acao_chamado: str = Form(""), dependencia_id: str = Form(""), cliente_id: str = Form(""), cliente_nome: str = Form(""), cliente_telefone: str = Form(""), cliente_telefone_manual: str = Form(""), cliente_empresa: str = Form(""), observacao: str = Form(""), data_tarefa: str = Form(""), abrir_manutencao: str = Form(""), usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    exigir_permissao(usuario, "pode_criar_tarefa")
    missao = db.query(Missao).filter(Missao.id == missao_id, Missao.projeto_id == projeto_id).first()
    if not missao:
        raise HTTPException(status_code=400, detail="Departamento / Etapa não pertence ao projeto selecionado.")
    dependencia = None
    if dependencia_id:
        dep = db.query(Tarefa).filter(Tarefa.id == int(dependencia_id), Tarefa.projeto_id == projeto_id).first()
        dependencia = dep.id if dep else None
    dias = None
    cliente_final = None
    if missao.finalidade == "cliente":
        if cliente_id:
            cliente = db.query(Cliente).filter(Cliente.id == int(cliente_id)).first()
            cliente_final = cliente.id if cliente else None
        if not cliente_final:
            telefone = limpar_telefone(cliente_telefone or cliente_telefone_manual)
            if not telefone_valido(telefone):
                raise HTTPException(status_code=400, detail="Telefone do cliente inválido.")
            cliente = db.query(Cliente).filter(Cliente.telefone == telefone).first()
            if not cliente:
                if not cliente_nome.strip():
                    raise HTTPException(status_code=400, detail="Informe o nome do cliente.")
                cliente = Cliente(nome=cliente_nome.strip(), telefone=telefone, empresa=cliente_empresa.strip() or None)
                db.add(cliente)
                db.flush()
            cliente_final = cliente.id
    validar_acao_para_chamado(status, acao_chamado)
    normalizar_ordem_tarefas(db, responsavel=responsavel)
    proxima_ordem = (db.query(func.max(Tarefa.ordem)).filter(Tarefa.responsavel == responsavel).scalar() or 0) + 1
    nome_tarefa = descricao.strip() or missao.nome
    if cliente_final:
        cliente_nome_final = db.query(Cliente).filter(Cliente.id == cliente_final).first()
        if cliente_nome_final:
            nome_tarefa = f"{missao.nome} - {cliente_nome_final.nome}"

    nova = Tarefa(projeto_id=projeto_id, missao_id=missao_id, descricao=nome_tarefa, responsavel=responsavel,
                  status=status, ordem=proxima_ordem, tipo="unica", dias_semana=dias, dependencia_id=dependencia,
                  cliente_id=cliente_final, observacao=nome_tarefa, data_tarefa=parse_data(data_tarefa) or date.today())
    db.add(nova)
    db.flush()
    registrar_historico(db, nova.id, "Criado em", nova.descricao, usuario, tipo="tarefa")
    criar_chamado_se_necessario(db, nova, None, usuario, acao_chamado)
    db.commit()
    if abrir_manutencao:
        return RedirectResponse(f"/organiza/tarefas/{nova.id}/manutencao", status_code=303)
    return RedirectResponse("/organiza", status_code=303)


@app.get("/organiza/tarefas/{tarefa_id}/editar", response_class=HTMLResponse)
def editar_tarefa(tarefa_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    tarefa = db.query(Tarefa).filter(Tarefa.id == tarefa_id).first()
    if not tarefa: raise HTTPException(status_code=404)
    projetos = db.query(Projeto).order_by(Projeto.nome).all()
    missoes = db.query(Missao).order_by(Missao.nome).all()
    tarefas = db.query(Tarefa).filter(Tarefa.id != tarefa_id).order_by(Tarefa.ordem.asc(), Tarefa.criado_em.asc(), Tarefa.id.asc()).all()
    clientes = db.query(Cliente).order_by(Cliente.nome).all()
    historicos = db.query(Historico).filter(Historico.tarefa_id == tarefa.id).order_by(Historico.criado_em.desc()).all()
    chamados = db.query(Chamado).filter(Chamado.tarefa_id == tarefa.id).order_by(Chamado.criado_em.desc()).all()
    return templates.TemplateResponse("organiza/tarefa_form.html", {
        "request": request, "usuario": usuario, "projetos": projetos, "missoes": missoes, "tarefa": tarefa,
        "tarefas": tarefas, "responsaveis": responsaveis_db(db), "status_validos": STATUS_VALIDOS,
        "projeto_id": tarefa.projeto_id, "missao_id": tarefa.missao_id, "dependencia_id": tarefa.dependencia_id, "clientes": clientes,
        "historicos": historicos, "chamados_tarefa": chamados, "departamentos_chamado": DEPARTAMENTOS_CHAMADO, "hoje": date.today()
    })


@app.post("/organiza/tarefas/{tarefa_id}/editar")
def salvar_tarefa(tarefa_id: int, projeto_id: int = Form(...), missao_id: int = Form(...), descricao: str = Form(""), responsavel: str = Form(...), status: str = Form("Pendente"), acao_chamado: str = Form(""), dependencia_id: str = Form(""), cliente_id: str = Form(""), cliente_nome: str = Form(""), cliente_telefone: str = Form(""), cliente_telefone_manual: str = Form(""), cliente_empresa: str = Form(""), observacao: str = Form(""), data_tarefa: str = Form(""), abrir_manutencao: str = Form(""), usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    tarefa = db.query(Tarefa).filter(Tarefa.id == tarefa_id).first()
    if not tarefa: raise HTTPException(status_code=404)
    missao = db.query(Missao).filter(Missao.id == missao_id, Missao.projeto_id == projeto_id).first()
    if not missao:
        raise HTTPException(status_code=400, detail="Departamento / Etapa não pertence ao projeto selecionado.")
    dependencia = None
    if dependencia_id:
        dep = db.query(Tarefa).filter(Tarefa.id == int(dependencia_id), Tarefa.projeto_id == projeto_id, Tarefa.id != tarefa_id).first()
        dependencia = dep.id if dep else None
    status_anterior = tarefa.status
    if status_anterior != status:
        validar_acao_para_chamado(status, acao_chamado)
    tarefa.projeto_id = projeto_id
    tarefa.missao_id = missao_id
    nome_tarefa = descricao.strip() or tarefa.descricao or missao.nome
    tarefa.descricao = nome_tarefa
    tarefa.observacao = nome_tarefa
    tarefa.responsavel = responsavel
    tarefa.status = status
    tarefa.tipo = "unica"
    tarefa.dias_semana = None
    tarefa.dependencia_id = dependencia
    tarefa.cliente_id = None
    if missao.finalidade == "cliente":
        if cliente_id:
            cliente = db.query(Cliente).filter(Cliente.id == int(cliente_id)).first()
            tarefa.cliente_id = cliente.id if cliente else None
        if not tarefa.cliente_id:
            telefone = limpar_telefone(cliente_telefone or cliente_telefone_manual)
            if not telefone_valido(telefone):
                raise HTTPException(status_code=400, detail="Telefone do cliente inválido.")
            cliente = db.query(Cliente).filter(Cliente.telefone == telefone).first()
            if not cliente:
                if not cliente_nome.strip():
                    raise HTTPException(status_code=400, detail="Informe o nome do cliente.")
                cliente = Cliente(nome=cliente_nome.strip(), telefone=telefone, empresa=cliente_empresa.strip() or None)
                db.add(cliente)
                db.flush()
            tarefa.cliente_id = cliente.id
    tarefa.data_tarefa = parse_data(data_tarefa) or tarefa.data_tarefa or date.today()
    if status_anterior != tarefa.status:
        registrar_historico(db, tarefa.id, f"Status alterado para {tarefa.status}", f"Antes: {status_anterior}", usuario, tipo="status")
    criar_chamado_se_necessario(db, tarefa, status_anterior, usuario, acao_chamado)
    db.commit()
    if abrir_manutencao:
        return RedirectResponse(f"/organiza/tarefas/{tarefa.id}/manutencao", status_code=303)
    return RedirectResponse("/organiza", status_code=303)



@app.get("/organiza/projetos", response_class=HTMLResponse)
def listar_projetos(request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    projetos = db.query(Projeto).order_by(Projeto.nome).all()
    percentuais = {p.id: calcular_percentual_projeto(p) for p in projetos}
    return templates.TemplateResponse("organiza/projetos.html", {"request": request, "usuario": usuario, "projetos": projetos, "percentuais": percentuais})


@app.get("/organiza/etapas", response_class=HTMLResponse)
def listar_etapas(request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    projetos = db.query(Projeto).order_by(Projeto.nome).all()
    return templates.TemplateResponse("organiza/etapas.html", {"request": request, "usuario": usuario, "projetos": projetos})


@app.get("/organiza/etapas/nova", response_class=HTMLResponse)
def escolher_projeto_etapa(request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    exigir_permissao(usuario, "pode_criar_etapa")
    projetos = db.query(Projeto).order_by(Projeto.nome).all()
    return templates.TemplateResponse("organiza/etapa_escolher_projeto.html", {"request": request, "usuario": usuario, "projetos": projetos})



@app.get("/organiza/agenda/nova", response_class=HTMLResponse)
def agenda_nova(request: Request, tarefa_id: Optional[int] = None, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    tarefas = db.query(Tarefa).filter(Tarefa.status != "Entregue", Tarefa.status != "Cancelado").order_by(Tarefa.criado_em.desc()).limit(80).all()
    return templates.TemplateResponse("organiza/agenda_form.html", {
        "request": request, "usuario": usuario, "titulo": "Novo compromisso | Organiza",
        "tarefas": tarefas, "tarefa_id": tarefa_id, "hoje": date.today()
    })


@app.post("/organiza/agenda/nova")
def agenda_salvar(titulo: str = Form(...), data_evento: str = Form(...), hora_evento: str = Form(""), tarefa_id: str = Form(""), observacao: str = Form(""), usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    evento = AgendaEvento(
        titulo=titulo.strip(),
        data_evento=parse_data(data_evento) or date.today(),
        hora_evento=hora_evento.strip() or None,
        tarefa_id=int(tarefa_id) if tarefa_id else None,
        observacao=observacao.strip() or None,
    )
    db.add(evento)
    db.commit()
    return RedirectResponse("/organiza", status_code=303)


@app.post("/organiza/agenda/{evento_id}/concluir")
def agenda_concluir(evento_id: int, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    evento = db.query(AgendaEvento).filter(AgendaEvento.id == evento_id).first()
    if not evento:
        raise HTTPException(status_code=404)
    evento.concluido = 1
    db.commit()
    return RedirectResponse("/organiza", status_code=303)


@app.get("/organiza/usuarios", response_class=HTMLResponse)
def listar_usuarios(request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    exigir_permissao(usuario, "pode_criar_usuario")
    usuarios = db.query(Usuario).order_by(Usuario.nome).all()
    return templates.TemplateResponse("organiza/usuarios.html", {"request": request, "usuario": usuario, "usuarios": usuarios})


@app.get("/organiza/usuarios/novo", response_class=HTMLResponse)
def novo_usuario(request: Request, usuario: Usuario = Depends(usuario_logado)):
    exigir_permissao(usuario, "pode_criar_usuario")
    return templates.TemplateResponse("organiza/usuario_form.html", {"request": request, "usuario": usuario, "editado": None})


@app.post("/organiza/usuarios/novo")
def criar_usuario(nome: str = Form(...), telefone: str = Form(...), senha: str = Form(...), email: str = Form(""), cargo: str = Form(""), is_admin: str = Form(""), pode_criar_tarefa: str = Form(""), pode_criar_projeto: str = Form(""), pode_criar_usuario: str = Form(""), pode_criar_etapa: str = Form(""), pode_cadastrar_cliente: str = Form(""), pode_cadastrar_item: str = Form(""), pode_acessar_compras: str = Form(""), pode_acessar_financeiro: str = Form(""), pode_cadastrar_banco: str = Form(""), pode_acessar_externo: str = Form(""), ativo: str = Form("1"), departamentos: Optional[List[str]] = Form(None), usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    exigir_permissao(usuario, "pode_criar_usuario")
    numero = limpar_telefone(telefone)
    if not telefone_valido(numero):
        raise HTTPException(status_code=400, detail="Telefone do usuário inválido. Use DDD + número.")
    if db.query(Usuario).filter(Usuario.nome == nome.strip()).first():
        raise HTTPException(status_code=400, detail="Já existe usuário com este nome.")
    admin = 1 if is_admin else 0
    db.add(Usuario(nome=nome.strip(), telefone=numero, email=email.strip() or None, cargo=cargo.strip() or None, senha_hash=gerar_hash_senha(senha), is_admin=admin, pode_criar_tarefa=1 if (pode_criar_tarefa or admin) else 0, pode_criar_projeto=1 if (pode_criar_projeto or admin) else 0, pode_criar_usuario=1 if (pode_criar_usuario or admin) else 0, pode_criar_etapa=1 if (pode_criar_etapa or admin) else 0, pode_cadastrar_cliente=1 if (pode_cadastrar_cliente or admin) else 0, pode_cadastrar_item=1 if (pode_cadastrar_item or admin) else 0, pode_acessar_compras=1 if (pode_acessar_compras or admin) else 0, pode_acessar_financeiro=1 if (pode_acessar_financeiro or admin) else 0, pode_cadastrar_banco=1 if (pode_cadastrar_banco or admin) else 0, pode_acessar_externo=1 if (pode_acessar_externo or admin) else 0, ativo=1 if ativo else 0, departamentos=",".join(departamentos or [])))
    db.commit()
    return RedirectResponse("/organiza/usuarios", status_code=303)


@app.get("/organiza/usuarios/{usuario_id}/editar", response_class=HTMLResponse)
def editar_usuario(usuario_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    exigir_permissao(usuario, "pode_criar_usuario")
    editado = db.query(Usuario).filter(Usuario.id == usuario_id).first()
    if not editado: raise HTTPException(status_code=404)
    return templates.TemplateResponse("organiza/usuario_form.html", {"request": request, "usuario": usuario, "editado": editado})


@app.post("/organiza/usuarios/{usuario_id}/editar")
def salvar_usuario(usuario_id: int, nome: str = Form(...), telefone: str = Form(...), senha: str = Form(""), email: str = Form(""), cargo: str = Form(""), is_admin: str = Form(""), pode_criar_tarefa: str = Form(""), pode_criar_projeto: str = Form(""), pode_criar_usuario: str = Form(""), pode_criar_etapa: str = Form(""), pode_cadastrar_cliente: str = Form(""), pode_cadastrar_item: str = Form(""), pode_acessar_compras: str = Form(""), pode_acessar_financeiro: str = Form(""), pode_cadastrar_banco: str = Form(""), pode_acessar_externo: str = Form(""), ativo: str = Form(""), departamentos: Optional[List[str]] = Form(None), usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    exigir_permissao(usuario, "pode_criar_usuario")
    editado = db.query(Usuario).filter(Usuario.id == usuario_id).first()
    if not editado: raise HTTPException(status_code=404)
    numero = limpar_telefone(telefone)
    if not telefone_valido(numero):
        raise HTTPException(status_code=400, detail="Telefone do usuário inválido. Use DDD + número.")
    conflito = db.query(Usuario).filter(Usuario.nome == nome.strip(), Usuario.id != usuario_id).first()
    if conflito:
        raise HTTPException(status_code=400, detail="Já existe usuário com este nome.")
    admin = 1 if is_admin else 0
    editado.nome = nome.strip()
    editado.telefone = numero
    editado.email = email.strip() or None
    editado.cargo = cargo.strip() or None
    editado.is_admin = admin
    editado.pode_criar_tarefa = 1 if (pode_criar_tarefa or admin) else 0
    editado.pode_criar_projeto = 1 if (pode_criar_projeto or admin) else 0
    editado.pode_criar_usuario = 1 if (pode_criar_usuario or admin) else 0
    editado.pode_criar_etapa = 1 if (pode_criar_etapa or admin) else 0
    editado.pode_cadastrar_cliente = 1 if (pode_cadastrar_cliente or admin) else 0
    editado.pode_cadastrar_item = 1 if (pode_cadastrar_item or admin) else 0
    editado.pode_acessar_compras = 1 if (pode_acessar_compras or admin) else 0
    editado.pode_acessar_financeiro = 1 if (pode_acessar_financeiro or admin) else 0
    editado.pode_cadastrar_banco = 1 if (pode_cadastrar_banco or admin) else 0
    editado.pode_acessar_externo = 1 if (pode_acessar_externo or admin) else 0
    editado.ativo = 1 if ativo else 0
    editado.departamentos = ",".join(departamentos or [])
    if senha.strip():
        editado.senha_hash = gerar_hash_senha(senha.strip())
    db.commit()
    return RedirectResponse("/organiza/usuarios", status_code=303)



@app.get("/organiza/clientes", response_class=HTMLResponse)
def listar_clientes(
    request: Request,
    busca: str = "",
    pacote: str = "",
    status: str = "",
    tipo: str = "",
    sem_equipamento: str = "",
    pagina: int = 1,
    por_pagina: int = 50,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db)
):
    """
    Lista de clientes otimizada.

    Antes, a tela carregava todos os clientes e depois acessava cliente.equipamentos
    dentro do HTML. Isso gerava muitas consultas no banco e deixava a página lenta
    no Render/Neon.

    Agora:
    - carrega no máximo 50 clientes por página;
    - traz os equipamentos junto com selectinload;
    - aplica filtros no banco sempre que possível;
    - evita varrer todos os clientes em Python.
    """
    pagina = max(int(pagina or 1), 1)
    por_pagina = min(max(int(por_pagina or 50), 10), 100)

    query = db.query(Cliente).options(selectinload(Cliente.equipamentos))
    termo = (busca or "").strip()

    if termo:
        like = f"%{termo}%"
        numero = limpar_telefone(termo)
        filtros = [Cliente.nome.ilike(like), Cliente.empresa.ilike(like), Cliente.cidade.ilike(like)]
        if numero:
            filtros.append(Cliente.telefone.like(f"%{numero}%"))
        query = query.filter(or_(*filtros))

    if pacote:
        query = query.filter(Cliente.pacote == pacote)

    if sem_equipamento:
        query = query.filter(~Cliente.equipamentos.any())

    if tipo:
        query = query.filter(Cliente.equipamentos.any(Equipamento.tipo == tipo))

    if status:
        query = query.filter(Cliente.equipamentos.any(Equipamento.status == status))

    total_filtrado = query.count()
    total_paginas = max((total_filtrado + por_pagina - 1) // por_pagina, 1)
    if pagina > total_paginas:
        pagina = total_paginas

    clientes = (
        query
        .order_by(Cliente.nome)
        .offset((pagina - 1) * por_pagina)
        .limit(por_pagina)
        .all()
    )

    total_clientes = db.query(Cliente).count()
    total_equipamentos = db.query(Equipamento).count()
    clientes_com_equipamento = db.query(Cliente.id).join(Equipamento).distinct().count()
    clientes_sem_equipamento = max(total_clientes - clientes_com_equipamento, 0)

    pacotes = [p[0] for p in db.query(Cliente.pacote).filter(Cliente.pacote.isnot(None)).distinct().order_by(Cliente.pacote).all() if p[0]]
    tipos = [t[0] for t in db.query(Equipamento.tipo).filter(Equipamento.tipo.isnot(None)).distinct().order_by(Equipamento.tipo).all() if t[0]]
    status_opcoes = [st[0] for st in db.query(Equipamento.status).filter(Equipamento.status.isnot(None)).distinct().order_by(Equipamento.status).all() if st[0]]

    return templates.TemplateResponse("organiza/clientes.html", {
        "request": request,
        "usuario": usuario,
        "clientes": clientes,
        "busca": busca,
        "pacote_atual": pacote,
        "status_atual": status,
        "tipo_atual": tipo,
        "sem_equipamento_atual": sem_equipamento,
        "pacotes": pacotes,
        "tipos": tipos,
        "status_opcoes": status_opcoes,
        "total_clientes": total_clientes,
        "total_equipamentos": total_equipamentos,
        "clientes_com_equipamento": clientes_com_equipamento,
        "clientes_sem_equipamento": clientes_sem_equipamento,
        "total_filtrado": total_filtrado,
        "pagina": pagina,
        "por_pagina": por_pagina,
        "total_paginas": total_paginas,
    })


@app.get("/organiza/clientes/novo", response_class=HTMLResponse)
def novo_cliente(request: Request, telefone: str = "", voltar: str = "/organiza", usuario: Usuario = Depends(usuario_logado)):
    exigir_permissao(usuario, "pode_cadastrar_cliente")
    return templates.TemplateResponse("organiza/cliente_form.html", {"request": request, "usuario": usuario, "cliente": None, "telefone": limpar_telefone(telefone), "voltar": voltar})


@app.post("/organiza/clientes/novo")
def criar_cliente(nome: str = Form(...), telefone: str = Form(...), empresa: str = Form(""), documento: str = Form(""), cep: str = Form(""), cidade: str = Form(""), bairro: str = Form(""), endereco: str = Form(""), email: str = Form(""), pacote: str = Form(""), falta_pacote: str = Form(""), plano: str = Form(""), observacao: str = Form(""), voltar: str = Form("/organiza/clientes"), usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    exigir_permissao(usuario, "pode_cadastrar_cliente")
    numero = limpar_telefone(telefone)
    if not telefone_valido(numero):
        raise HTTPException(status_code=400, detail="Telefone inválido. Use DDD + número.")
    existe = db.query(Cliente).filter(Cliente.telefone == numero).first()
    if existe:
        return RedirectResponse(voltar or "/organiza", status_code=303)
    cliente = Cliente(
        nome=nome.strip(),
        telefone=numero,
        empresa=empresa.strip() or None,
        documento=documento.strip() or None,
        cep=cep.strip() or None,
        cidade=cidade.strip() or None,
        bairro=bairro.strip() or None,
        endereco=endereco.strip() or None,
        email=email.strip() or None,
        pacote=pacote.strip() or None,
        falta_pacote=int(falta_pacote) if str(falta_pacote).strip().isdigit() else None,
        plano=plano.strip() or None,
        observacao=observacao.strip() or None,
    )
    db.add(cliente)
    db.commit()
    return RedirectResponse(voltar or "/organiza/clientes", status_code=303)




@app.get("/organiza/clientes/{cliente_id}", response_class=HTMLResponse)
def detalhe_cliente(cliente_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    cliente = db.query(Cliente).filter(Cliente.id == cliente_id).first()
    if not cliente:
        raise HTTPException(status_code=404)
    tarefas = db.query(Tarefa).filter(Tarefa.cliente_id == cliente.id).order_by(Tarefa.criado_em.desc()).limit(20).all()
    return templates.TemplateResponse("organiza/cliente_detalhe.html", {
        "request": request,
        "usuario": usuario,
        "cliente": cliente,
        "tarefas": tarefas,
    })


@app.get("/organiza/clientes/{cliente_id}/editar", response_class=HTMLResponse)
def editar_cliente(cliente_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    exigir_permissao(usuario, "pode_cadastrar_cliente")
    cliente = db.query(Cliente).filter(Cliente.id == cliente_id).first()
    if not cliente:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse("organiza/cliente_form.html", {"request": request, "usuario": usuario, "cliente": cliente, "telefone": cliente.telefone, "voltar": f"/organiza/clientes/{cliente.id}"})


@app.post("/organiza/clientes/{cliente_id}/editar")
def salvar_cliente(cliente_id: int, nome: str = Form(...), telefone: str = Form(...), empresa: str = Form(""), documento: str = Form(""), cep: str = Form(""), cidade: str = Form(""), bairro: str = Form(""), endereco: str = Form(""), email: str = Form(""), pacote: str = Form(""), falta_pacote: str = Form(""), plano: str = Form(""), observacao: str = Form(""), voltar: str = Form("/organiza/clientes"), usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    exigir_permissao(usuario, "pode_cadastrar_cliente")
    cliente = db.query(Cliente).filter(Cliente.id == cliente_id).first()
    if not cliente:
        raise HTTPException(status_code=404)
    numero = limpar_telefone(telefone)
    if not telefone_valido(numero):
        raise HTTPException(status_code=400, detail="Telefone inválido. Use DDD + número.")
    existe = db.query(Cliente).filter(Cliente.telefone == numero, Cliente.id != cliente.id).first()
    if existe:
        raise HTTPException(status_code=400, detail="Já existe outro cliente com este telefone.")
    cliente.nome = nome.strip()
    cliente.telefone = numero
    cliente.empresa = empresa.strip() or None
    cliente.documento = documento.strip() or None
    cliente.cep = cep.strip() or None
    cliente.cidade = cidade.strip() or None
    cliente.bairro = bairro.strip() or None
    cliente.endereco = endereco.strip() or None
    cliente.email = email.strip() or None
    cliente.pacote = pacote.strip() or None
    cliente.falta_pacote = int(falta_pacote) if str(falta_pacote).strip().isdigit() else None
    cliente.plano = plano.strip() or None
    cliente.observacao = observacao.strip() or None
    db.commit()
    return RedirectResponse(voltar or f"/organiza/clientes/{cliente.id}", status_code=303)


@app.get("/organiza/clientes/{cliente_id}/equipamentos/novo", response_class=HTMLResponse)
def novo_equipamento(cliente_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    cliente = db.query(Cliente).filter(Cliente.id == cliente_id).first()
    if not cliente:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse("organiza/equipamento_form.html", {"request": request, "usuario": usuario, "cliente": cliente, "equipamento": None})


@app.post("/organiza/clientes/{cliente_id}/equipamentos/novo")
def criar_equipamento(cliente_id: int, tipo: str = Form(""), modelo: str = Form(""), pacote: str = Form(""), falta_pacote: str = Form(""), plano: str = Form(""), valor: str = Form(""), pago: str = Form(""), falta: str = Form(""), data_compra: str = Form(""), previsao_entrega: str = Form(""), maquina: str = Form(""), rede_instalada: str = Form(""), anydesk: str = Form(""), status: str = Form("Ativo"), observacao: str = Form(""), usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    cliente = db.query(Cliente).filter(Cliente.id == cliente_id).first()
    if not cliente:
        raise HTTPException(status_code=404)
    def parse_data(v):
        try:
            return datetime.strptime(v, "%Y-%m-%d").date() if v else None
        except Exception:
            return None
    equipamento = Equipamento(
        cliente_id=cliente.id, tipo=tipo.strip() or None, modelo=modelo.strip() or None,
        pacote=pacote.strip() or None, falta_pacote=int(falta_pacote) if str(falta_pacote).strip().isdigit() else None,
        plano=plano.strip() or None, valor=valor.strip() or None, pago=pago.strip() or None, falta=falta.strip() or None,
        data_compra=parse_data(data_compra), previsao_entrega=parse_data(previsao_entrega),
        maquina=maquina.strip() or None, rede_instalada=rede_instalada.strip() or None, anydesk=anydesk.strip() or None,
        status=status.strip() or "Ativo", observacao=observacao.strip() or None
    )
    db.add(equipamento)
    db.commit()
    return RedirectResponse(f"/organiza/clientes/{cliente.id}", status_code=303)


@app.get("/organiza/clientes/{cliente_id}/equipamentos/{equipamento_id}/editar", response_class=HTMLResponse)
def editar_equipamento(cliente_id: int, equipamento_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    cliente = db.query(Cliente).filter(Cliente.id == cliente_id).first()
    equipamento = db.query(Equipamento).filter(Equipamento.id == equipamento_id, Equipamento.cliente_id == cliente_id).first()
    if not cliente or not equipamento:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse("organiza/equipamento_form.html", {"request": request, "usuario": usuario, "cliente": cliente, "equipamento": equipamento})


@app.post("/organiza/clientes/{cliente_id}/equipamentos/{equipamento_id}/editar")
def salvar_equipamento(cliente_id: int, equipamento_id: int, tipo: str = Form(""), modelo: str = Form(""), pacote: str = Form(""), falta_pacote: str = Form(""), plano: str = Form(""), valor: str = Form(""), pago: str = Form(""), falta: str = Form(""), data_compra: str = Form(""), previsao_entrega: str = Form(""), maquina: str = Form(""), rede_instalada: str = Form(""), anydesk: str = Form(""), status: str = Form("Ativo"), observacao: str = Form(""), usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    cliente = db.query(Cliente).filter(Cliente.id == cliente_id).first()
    equipamento = db.query(Equipamento).filter(Equipamento.id == equipamento_id, Equipamento.cliente_id == cliente_id).first()
    if not cliente or not equipamento:
        raise HTTPException(status_code=404)
    def parse_data(v):
        try:
            return datetime.strptime(v, "%Y-%m-%d").date() if v else None
        except Exception:
            return None
    equipamento.tipo = tipo.strip() or None
    equipamento.modelo = modelo.strip() or None
    equipamento.pacote = pacote.strip() or None
    equipamento.falta_pacote = int(falta_pacote) if str(falta_pacote).strip().isdigit() else None
    equipamento.plano = plano.strip() or None
    equipamento.valor = valor.strip() or None
    equipamento.pago = pago.strip() or None
    equipamento.falta = falta.strip() or None
    equipamento.data_compra = parse_data(data_compra)
    equipamento.previsao_entrega = parse_data(previsao_entrega)
    equipamento.maquina = maquina.strip() or None
    equipamento.rede_instalada = rede_instalada.strip() or None
    equipamento.anydesk = anydesk.strip() or None
    equipamento.status = status.strip() or "Ativo"
    equipamento.observacao = observacao.strip() or None
    db.commit()
    return RedirectResponse(f"/organiza/clientes/{cliente.id}", status_code=303)

@app.get("/api/clientes/buscar")
def buscar_cliente(q: str = "", telefone: str = "", usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    termo = (q or telefone or "").strip()
    numero = limpar_telefone(termo)
    query = db.query(Cliente)
    if numero:
        query = query.filter(Cliente.telefone.like(f"%{numero}%"))
    elif termo:
        like = f"%{termo}%"
        query = query.filter(or_(Cliente.nome.ilike(like), Cliente.empresa.ilike(like)))
    else:
        return {"ok": False, "clientes": []}
    clientes = query.order_by(Cliente.nome).limit(8).all()
    return {"ok": bool(clientes), "clientes": [{"id": c.id, "nome": c.nome, "telefone": c.telefone, "telefone_formatado": formatar_telefone(c.telefone), "empresa": c.empresa or ""} for c in clientes]}



@app.get("/organiza/tarefas/{tarefa_id}/chamados/novo", response_class=HTMLResponse)
def novo_chamado_tarefa(tarefa_id: int, request: Request, departamento: str = "", usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    tarefa = db.query(Tarefa).filter(Tarefa.id == tarefa_id).first()
    if not tarefa:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse("organiza/chamado_novo.html", {"request": request, "usuario": usuario, "tarefa": tarefa, "departamentos": DEPARTAMENTOS_CHAMADO, "departamento_atual": departamento})


@app.post("/organiza/tarefas/{tarefa_id}/chamados/novo")
def criar_chamado_tarefa(tarefa_id: int, departamento: str = Form(...), descricao: str = Form(...), usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    tarefa = db.query(Tarefa).filter(Tarefa.id == tarefa_id).first()
    if not tarefa:
        raise HTTPException(status_code=404)
    if departamento not in DEPARTAMENTOS_CHAMADO:
        raise HTTPException(status_code=400, detail="Tipo de chamado inválido.")
    if not descricao.strip():
        raise HTTPException(status_code=400, detail="A observação do chamado é obrigatória.")
    aberto = db.query(Chamado).filter(Chamado.tarefa_id == tarefa.id, Chamado.departamento == departamento, Chamado.status != "Concluído").first()
    if aberto:
        return RedirectResponse(f"/organiza/chamados/{aberto.id}", status_code=303)
    chamado = Chamado(tarefa_id=tarefa.id, departamento=departamento, descricao=descricao.strip(), criado_por=usuario.nome, status="Aberto")
    db.add(chamado)
    status_anterior = tarefa.status
    tarefa.status = status_aguardando_departamento(departamento)
    registrar_historico(db, tarefa.id, f"Chamado aberto para {departamento}", descricao, usuario, tipo="chamado")
    registrar_historico(db, tarefa.id, f"Status alterado para {tarefa.status}", f"Antes: {status_anterior}", usuario, tipo="status")
    db.commit()
    return RedirectResponse("/organiza", status_code=303)


@app.get("/organiza/chamados", response_class=HTMLResponse)
def listar_chamados(request: Request, departamento: str = "", usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    deps = departamentos_usuario(usuario)
    query = db.query(Chamado).filter(Chamado.status != "Concluído")
    if departamento:
        query = query.filter(Chamado.departamento == departamento)
    elif not usuario.is_admin:
        if deps:
            query = query.filter(Chamado.departamento.in_(deps))
        else:
            query = query.filter(Chamado.id == -1)
    chamados = query.order_by(Chamado.criado_em.desc()).all()
    return templates.TemplateResponse("organiza/chamados.html", {"request": request, "usuario": usuario, "chamados": chamados, "departamentos": DEPARTAMENTOS_CHAMADO, "departamento_atual": departamento})


@app.get("/organiza/chamados/{chamado_id}", response_class=HTMLResponse)
def abrir_chamado(chamado_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    chamado = db.query(Chamado).filter(Chamado.id == chamado_id).first()
    if not chamado:
        raise HTTPException(status_code=404)
    if not usuario_tem_departamento(usuario, chamado.departamento):
        raise HTTPException(status_code=403, detail="Este chamado pertence a outro departamento.")
    externos = db.query(Externo).order_by(Externo.nome).all()
    historicos = db.query(Historico).filter(Historico.tarefa_id == chamado.tarefa_id).order_by(Historico.criado_em.desc()).all()
    return templates.TemplateResponse("organiza/chamado_form.html", {"request": request, "usuario": usuario, "chamado": chamado, "externos": externos, "status_chamado": STATUS_CHAMADO, "historicos": historicos})


@app.post("/organiza/chamados/{chamado_id}")
def salvar_chamado(chamado_id: int, descricao: str = Form(""), status: str = Form("Aberto"), fornecedor: str = Form(""), valor: str = Form(""), previsao_entrega: str = Form(""), data_recebimento: str = Form(""), externo_id: str = Form(""), externo_nome: str = Form(""), externo_telefone: str = Form(""), data_envio: str = Form(""), data_devolucao: str = Form(""), observacao: str = Form(""), usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    chamado = db.query(Chamado).filter(Chamado.id == chamado_id).first()
    if not chamado:
        raise HTTPException(status_code=404)
    if not usuario_tem_departamento(usuario, chamado.departamento):
        raise HTTPException(status_code=403, detail="Este chamado pertence a outro departamento.")
    status_anterior = chamado.status
    descricao_anterior = chamado.descricao
    chamado.descricao = descricao.strip() or chamado.descricao
    chamado.status = status if status in STATUS_CHAMADO else chamado.status
    chamado.fornecedor = fornecedor.strip() or None
    chamado.valor = valor.strip() or None
    chamado.previsao_entrega = parse_data(previsao_entrega)
    chamado.data_recebimento = parse_data(data_recebimento)
    chamado.data_envio = parse_data(data_envio)
    chamado.data_devolucao = parse_data(data_devolucao)
    chamado.observacao = observacao.strip() or None
    if chamado.status != status_anterior:
        registrar_historico(db, chamado.tarefa_id, f"Chamado {chamado.departamento} alterado para {chamado.status}", f"Antes: {status_anterior}", usuario, tipo="chamado")

        # REGRA ORGANIZA 6.0:
        # Quando qualquer chamado é concluído, a tarefa de origem volta para Pendente.
        # Isso devolve a tarefa ao responsável original para continuar o fluxo.
        if chamado.status == "Concluído":
            chamado.finalizado_por = usuario.nome
            chamado.concluido_em = datetime.now()
            status_tarefa_anterior = chamado.tarefa.status
            chamado.tarefa.status = "Pendente"
            registrar_historico(
                db,
                chamado.tarefa_id,
                "Status alterado para Pendente",
                f"Antes: {status_tarefa_anterior}. Chamado concluído e tarefa devolvida ao responsável original.",
                usuario,
                tipo="status",
            )
    elif chamado.descricao != descricao_anterior:
        registrar_historico(db, chamado.tarefa_id, f"Chamado {chamado.departamento} atualizado", chamado.descricao, usuario, tipo="chamado")

    if chamado.departamento == "Externo":
        if externo_id:
            chamado.externo_id = int(externo_id)
        elif externo_nome.strip() and telefone_valido(externo_telefone):
            numero = limpar_telefone(externo_telefone)
            externo = db.query(Externo).filter(Externo.telefone == numero).first()
            if not externo:
                externo = Externo(nome=externo_nome.strip(), telefone=numero)
                db.add(externo)
                db.flush()
            chamado.externo_id = externo.id
    db.commit()
    return RedirectResponse("/organiza", status_code=303)


@app.post("/organiza/chamados/{chamado_id}/concluir")
def concluir_chamado(chamado_id: int, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    chamado = db.query(Chamado).filter(Chamado.id == chamado_id).first()
    if not chamado:
        raise HTTPException(status_code=404)
    if not usuario_tem_departamento(usuario, chamado.departamento):
        raise HTTPException(status_code=403, detail="Este chamado pertence a outro departamento.")
    if chamado.departamento == "Compras" and not chamado.data_recebimento:
        raise HTTPException(status_code=400, detail="Compras só pode concluir depois de informar a data de recebimento.")
    chamado.status = "Concluído"
    chamado.finalizado_por = usuario.nome
    chamado.concluido_em = datetime.now()
    chamado.tarefa.status = "Pendente"
    registrar_historico(db, chamado.tarefa_id, f"Chamado encerrado: {chamado.departamento}", chamado.descricao, usuario, tipo="chamado")
    registrar_historico(db, chamado.tarefa_id, "Status alterado para Pendente", "Chamado concluído e tarefa devolvida ao responsável original.", usuario, tipo="status")
    db.commit()
    return RedirectResponse("/organiza", status_code=303)


@app.get("/api/externos/buscar")
def buscar_externos(q: str = "", usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    termo = (q or "").strip()
    numero = limpar_telefone(termo)
    if not termo:
        return {"ok": False, "externos": []}
    query = db.query(Externo)
    if numero:
        query = query.filter(Externo.telefone.like(f"%{numero}%"))
    else:
        like = f"%{termo}%"
        query = query.filter(or_(Externo.nome.ilike(like), Externo.empresa.ilike(like)))
    externos = query.order_by(Externo.nome).limit(8).all()
    return {"ok": bool(externos), "externos": [{"id": e.id, "nome": e.nome, "telefone": e.telefone, "telefone_formatado": formatar_telefone(e.telefone), "empresa": e.empresa or ""} for e in externos]}


@app.post("/organiza/tarefas/{tarefa_id}/ordem")
def alterar_ordem_tarefa(tarefa_id: int, direcao: str = Form(...), voltar: str = Form("/organiza"), usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    tarefa = db.query(Tarefa).filter(Tarefa.id == tarefa_id).first()
    if not tarefa:
        raise HTTPException(status_code=404)
    if not usuario.is_admin and tarefa.responsavel != usuario.nome:
        raise HTTPException(status_code=403, detail="Você só pode ordenar tarefas sob sua responsabilidade.")
    if direcao in {"subir", "descer", "topo", "final"}:
        trocar_ordem_tarefa(db, tarefa, direcao)
        db.commit()
    return RedirectResponse(voltar or "/organiza", status_code=303)


@app.post("/organiza/tarefas/{tarefa_id}/excluir")
def excluir_tarefa(tarefa_id: int, voltar: str = Form("/organiza"), usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    tarefa = db.query(Tarefa).filter(Tarefa.id == tarefa_id).first()
    if not tarefa:
        raise HTTPException(status_code=404)
    if not usuario.is_admin and tarefa.responsavel != usuario.nome:
        raise HTTPException(status_code=403, detail="Você só pode excluir tarefas sob sua responsabilidade.")
    destino = voltar or f"/organiza/projetos/{tarefa.projeto_id}"
    responsavel = tarefa.responsavel
    db.delete(tarefa)
    db.flush()
    normalizar_ordem_tarefas(db, responsavel=responsavel)
    db.commit()
    return RedirectResponse(destino, status_code=303)


@app.post("/organiza/tarefas/{tarefa_id}/status")
def alterar_status(tarefa_id: int, status: str = Form(...), voltar: str = Form("/organiza"), usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    tarefa = db.query(Tarefa).filter(Tarefa.id == tarefa_id).first()
    if not tarefa: raise HTTPException(status_code=404)
    if status in STATUS_VALIDOS:
        status_anterior = tarefa.status
        tarefa.status = status
        if status_anterior != status:
            registrar_historico(db, tarefa.id, f"Status alterado para {status}", f"Antes: {status_anterior}", usuario, tipo="status")
        criar_chamado_se_necessario(db, tarefa, status_anterior, usuario)
        db.commit()
    return RedirectResponse(voltar or "/organiza", status_code=303)


# =========================
# ROTAS ORGANIZA 7.1
# Manutenção / Compras / Financeiro / Banco
# =========================

def _valor(valor: str) -> str:
    return (valor or "").strip()


def _numero_decimal(valor: str) -> float:
    texto = (valor or "0").strip().replace(".", "").replace(",", ".")
    try:
        return float(texto)
    except ValueError:
        return 0.0


def _moeda_br(valor: float) -> str:
    return f"{valor:.2f}".replace(".", ",")


def _recalcular_orcamento_manutencao(manutencao: Manutencao):
    total = 0.0
    for item in manutencao.itens:
        total += _numero_decimal(item.valor_total)
    manutencao.valor_orcamento = _moeda_br(total) if total else None


def obter_ou_criar_manutencao_da_tarefa(db: Session, tarefa: Tarefa, usuario: Usuario) -> Manutencao:
    if tarefa.manutencao:
        return tarefa.manutencao
    nome_cliente = tarefa.cliente.nome if tarefa.cliente else "Cliente não informado"
    telefone = tarefa.cliente.telefone if tarefa.cliente else None
    manutencao = Manutencao(
        tarefa_id=tarefa.id,
        cliente_id=tarefa.cliente_id,
        nome_cliente=nome_cliente,
        telefone=telefone,
        problema=tarefa.descricao,
        responsavel=tarefa.responsavel or usuario.nome,
        observacao=tarefa.observacao,
    )
    db.add(manutencao)
    db.flush()
    registrar_historico(db, tarefa.id, "Manutenção vinculada à tarefa", "O módulo de manutenção foi aberto dentro desta tarefa.", usuario, tipo="manutencao")
    return manutencao


def _criar_lancamento_banco(db: Session, conta: ContaFinanceira):
    if not conta.banco:
        return
    ja_existe = db.query(BancoMovimento).filter(BancoMovimento.conta_id == conta.id).first()
    if ja_existe:
        return
    tipo_movimento = "entrada" if conta.tipo == "receber" else "saida"
    db.add(BancoMovimento(
        conta_id=conta.id,
        tipo=tipo_movimento,
        banco=conta.banco,
        categoria=conta.categoria,
        descricao=conta.descricao,
        valor=conta.valor,
        data=conta.pago_em or date.today(),
        conciliado=1,
    ))




@app.get("/organiza/produtos-servicos", response_class=HTMLResponse)
def produtos_servicos(request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    exigir_permissao(usuario, "pode_cadastrar_item")
    itens = db.query(ProdutoServico).order_by(ProdutoServico.ativo.desc(), ProdutoServico.nome.asc()).all()
    return templates.TemplateResponse("organiza/produtos_servicos.html", {
        "request": request, "usuario": usuario, "itens": itens, "titulo": "Produtos e serviços | Organiza",
    })


@app.post("/organiza/produtos-servicos/novo")
def produto_servico_criar(nome: str = Form(...), categoria: str = Form(""), tipo: str = Form("Produto"), valor_padrao: str = Form(""), observacao: str = Form(""), usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    exigir_permissao(usuario, "pode_cadastrar_item")
    tipo_final = tipo if tipo in {"Produto", "Serviço"} else "Produto"
    db.add(ProdutoServico(nome=nome.strip(), categoria=_valor(categoria), tipo=tipo_final, valor_padrao=_valor(valor_padrao), observacao=_valor(observacao), ativo=1))
    db.commit()
    return RedirectResponse("/organiza/produtos-servicos", status_code=303)


@app.post("/organiza/produtos-servicos/{item_id}/alternar")
def produto_servico_alternar(item_id: int, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    item = db.query(ProdutoServico).filter(ProdutoServico.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404)
    item.ativo = 0 if item.ativo else 1
    db.commit()
    return RedirectResponse("/organiza/produtos-servicos", status_code=303)


@app.get("/organiza/tarefas/{tarefa_id}/manutencao", response_class=HTMLResponse)
def tarefa_manutencao(tarefa_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    tarefa = db.query(Tarefa).filter(Tarefa.id == tarefa_id).first()
    if not tarefa:
        raise HTTPException(status_code=404)
    manutencao = obter_ou_criar_manutencao_da_tarefa(db, tarefa, usuario)
    db.commit()
    produtos = db.query(ProdutoServico).filter(ProdutoServico.ativo == 1).order_by(ProdutoServico.nome.asc()).all()
    bancos = db.query(BancoConta).filter(BancoConta.ativo == 1).order_by(BancoConta.nome.asc()).all()
    historicos = db.query(Historico).filter(Historico.tarefa_id == tarefa.id).order_by(Historico.criado_em.desc()).all()
    orcamento_enviado = any(h.titulo in {"Orçamento enviado ao cliente", "Cliente aprovou o orçamento"} for h in historicos)
    mensagens = db.query(Mensagem).filter(Mensagem.tarefa_id == tarefa.id).order_by(Mensagem.criado_em.desc()).all()
    compras_vinculadas = db.query(Compra).filter(Compra.manutencao_id == manutencao.id).order_by(Compra.criado_em.desc()).all()
    return templates.TemplateResponse("organiza/manutencao_tarefa.html", {
        "request": request,
        "usuario": usuario,
        "tarefa": tarefa,
        "manutencao": manutencao,
        "produtos": produtos,
        "bancos": bancos,
        "historicos": historicos,
        "mensagens": mensagens,
        "compras_vinculadas": compras_vinculadas,
        "status_manutencao": STATUS_MANUTENCAO,
        "status_validos": STATUS_VALIDOS,
        "orcamento_enviado": orcamento_enviado,
        "titulo": "Manutenção da tarefa | Organiza", "hoje": date.today(), "hora_atual": datetime.now().strftime("%H:%M"),
        "copiar": request.query_params.get("copiar", ""),
    })


@app.post("/organiza/tarefas/{tarefa_id}/manutencao/salvar")
def tarefa_manutencao_salvar(tarefa_id: int, equipamento: str = Form(""), problema: str = Form(""), status_manutencao: str = Form("Recebida"), status_tarefa: str = Form(""), forma_pagamento: str = Form(""), data_orcamento: str = Form(""), hora_orcamento: str = Form(""), prazo_dias: str = Form("20"), data_conclusao: str = Form(""), data_retirada: str = Form(""), observacao: str = Form(""), usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    tarefa = db.query(Tarefa).filter(Tarefa.id == tarefa_id).first()
    if not tarefa:
        raise HTTPException(status_code=404)
    manutencao = obter_ou_criar_manutencao_da_tarefa(db, tarefa, usuario)

    manutencao.equipamento = _valor(equipamento)
    manutencao.problema = problema.strip() or tarefa.descricao
    manutencao.forma_pagamento = _valor(forma_pagamento)
    manutencao.data_orcamento = parse_data(data_orcamento) or manutencao.data_orcamento or date.today()
    manutencao.hora_orcamento = _valor(hora_orcamento) or manutencao.hora_orcamento or datetime.now().strftime("%H:%M")
    manutencao.prazo_dias = _valor(prazo_dias) or "20"
    manutencao.observacao = _valor(observacao)
    tarefa.observacao = observacao.strip() or tarefa.observacao

    # Primeiro salvamento do orçamento: envia para o cliente e sai do painel de edição.
    tarefa.status = "Aguardando Cliente"
    manutencao.status = "Orçamento Cliente"
    registrar_historico(db, tarefa.id, "Orçamento enviado ao cliente", "Status: Aguardando Cliente (Orçamento)", usuario, tipo="manutencao")
    db.commit()
    return RedirectResponse(f"/organiza/tarefas/{tarefa_id}/manutencao?copiar=orcamento", status_code=303)


@app.post("/organiza/tarefas/{tarefa_id}/manutencao/itens")
def tarefa_manutencao_adicionar_item(tarefa_id: int, produto_servico_id: str = Form(""), nome_manual: str = Form(""), quantidade: str = Form("1"), valor_unitario: str = Form(""), observacao: str = Form(""), usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    tarefa = db.query(Tarefa).filter(Tarefa.id == tarefa_id).first()
    if not tarefa:
        raise HTTPException(status_code=404)
    manutencao = obter_ou_criar_manutencao_da_tarefa(db, tarefa, usuario)
    produto = None
    if produto_servico_id:
        produto = db.query(ProdutoServico).filter(ProdutoServico.id == int(produto_servico_id)).first()
    nome = produto.nome if produto else ""
    if not nome:
        raise HTTPException(status_code=400, detail="Selecione um produto/serviço cadastrado.")
    valor = _valor(valor_unitario) or (produto.valor_padrao if produto else "")
    qtd = _valor(quantidade) or "1"
    total = _moeda_br(_numero_decimal(qtd) * _numero_decimal(valor)) if valor else ""
    item = ManutencaoItem(
        manutencao_id=manutencao.id,
        produto_servico_id=produto.id if produto else None,
        nome=nome,
        tipo=produto.tipo if produto else "Produto",
        quantidade=qtd,
        valor_unitario=valor,
        valor_total=total,
        observacao=_valor(observacao),
    )
    db.add(item)
    db.flush()
    _recalcular_orcamento_manutencao(manutencao)

    # Fluxo simples: adicionou item, o orçamento já está pronto para enviar ao cliente.
    status_anterior = tarefa.status
    tarefa.status = "Pendente"
    manutencao.status = "Orçamento Cliente"
    manutencao.data_orcamento = manutencao.data_orcamento or date.today()
    manutencao.hora_orcamento = manutencao.hora_orcamento or datetime.now().strftime("%H:%M")
    manutencao.prazo_dias = manutencao.prazo_dias or "20"

    # Item adicionado não entra na timeline operacional da OS.
    db.commit()
    return RedirectResponse(f"/organiza/tarefas/{tarefa_id}/manutencao", status_code=303)


@app.post("/organiza/tarefas/{tarefa_id}/manutencao/itens-rapido")
def tarefa_manutencao_adicionar_item_rapido(
    tarefa_id: int,
    produto_servico_id: str = Form(""),
    quantidade: str = Form("1"),
    valor_unitario: str = Form(""),
    observacao: str = Form(""),
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db)
):
    """Adiciona item ao orçamento sem recarregar a tela inteira.

    Usado pela tela de orçamento para dar resposta imediata ao técnico.
    A rota antiga continua existindo como fallback.
    """
    tarefa = db.query(Tarefa).filter(Tarefa.id == tarefa_id).first()
    if not tarefa:
        return JSONResponse({"ok": False, "erro": "Tarefa não encontrada."}, status_code=404)

    manutencao = obter_ou_criar_manutencao_da_tarefa(db, tarefa, usuario)

    produto = None
    if produto_servico_id:
        produto = db.query(ProdutoServico).filter(ProdutoServico.id == int(produto_servico_id)).first()

    if not produto:
        return JSONResponse({"ok": False, "erro": "Selecione um produto/serviço cadastrado."}, status_code=400)

    valor = _valor(valor_unitario) or (produto.valor_padrao if produto else "")
    qtd = _valor(quantidade) or "1"
    total = _moeda_br(_numero_decimal(qtd) * _numero_decimal(valor)) if valor else ""

    item = ManutencaoItem(
        manutencao_id=manutencao.id,
        produto_servico_id=produto.id,
        nome=produto.nome,
        tipo=produto.tipo,
        quantidade=qtd,
        valor_unitario=valor,
        valor_total=total,
        observacao=_valor(observacao),
    )
    db.add(item)
    db.flush()

    _recalcular_orcamento_manutencao(manutencao)

    tarefa.status = "Pendente"
    manutencao.status = "Orçamento Cliente"
    manutencao.data_orcamento = manutencao.data_orcamento or date.today()
    manutencao.hora_orcamento = manutencao.hora_orcamento or datetime.now().strftime("%H:%M")
    manutencao.prazo_dias = manutencao.prazo_dias or "20"

    db.commit()
    db.refresh(item)
    db.refresh(manutencao)

    return {
        "ok": True,
        "item": {
            "id": item.id,
            "nome": item.nome or "",
            "quantidade": item.quantidade or "1",
            "valor_unitario": item.valor_unitario or "",
            "valor_total": item.valor_total or "",
            "aprovado": item.aprovado or 0,
        },
        "total_orcamento": manutencao.valor_orcamento or "0,00",
    }


@app.post("/organiza/manutencao/itens/{item_id}/excluir")
def tarefa_manutencao_excluir_item(item_id: int, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    item = db.query(ManutencaoItem).filter(ManutencaoItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404)
    tarefa_id = item.manutencao.tarefa_id
    manutencao = item.manutencao
    # Remoção de item não entra na timeline operacional da OS.
    db.delete(item)
    db.flush()
    _recalcular_orcamento_manutencao(manutencao)
    db.commit()
    return RedirectResponse(f"/organiza/tarefas/{tarefa_id}/manutencao", status_code=303)


@app.post("/organiza/manutencao/itens/{item_id}/atualizar")
def tarefa_manutencao_atualizar_item(item_id: int, nome: str = Form(""), quantidade: str = Form("1"), valor_unitario: str = Form(""), usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    item = db.query(ManutencaoItem).filter(ManutencaoItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404)
    tarefa_id = item.manutencao.tarefa_id
    item.nome = nome.strip() or item.nome
    item.quantidade = _valor(quantidade) or "1"
    item.valor_unitario = _valor(valor_unitario)
    item.valor_total = _moeda_br(_numero_decimal(item.quantidade) * _numero_decimal(item.valor_unitario)) if item.valor_unitario else ""
    _recalcular_orcamento_manutencao(item.manutencao)
    # Atualização de item não entra na timeline operacional da OS.
    db.commit()
    return RedirectResponse(f"/organiza/tarefas/{tarefa_id}/manutencao", status_code=303)


@app.post("/organiza/manutencao/itens/{item_id}/marcar")
def tarefa_manutencao_marcar_item(item_id: int, acao: str = Form(...), usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    item = db.query(ManutencaoItem).filter(ManutencaoItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404)
    tarefa_id = item.manutencao.tarefa_id
    if acao == "aprovado":
        item.aprovado = 1
        titulo = "Item aprovado"
    elif acao == "nao_aprovado":
        item.aprovado = -1
        item.comprar = 0
        item.externo = 0
        titulo = "Item não aprovado"
    elif acao == "sem_decisao":
        item.aprovado = 0
        item.comprar = 0
        item.externo = 0
        titulo = "Item sem decisão"
    elif acao == "comprar":
        item.comprar = 0 if item.comprar else 1
        item.aprovado = 0
        item.externo = 0
        titulo = "Item marcado para compras" if item.comprar else "Item removido de compras"
    elif acao == "externo":
        item.externo = 0 if item.externo else 1
        item.aprovado = 0
        item.comprar = 0
        titulo = "Item marcado para externo" if item.externo else "Item removido de externo"
    else:
        raise HTTPException(status_code=400)
    # Marcação interna de item não entra na timeline operacional da OS.
    db.commit()
    return RedirectResponse(f"/organiza/tarefas/{tarefa_id}/manutencao", status_code=303)


@app.post("/organiza/tarefas/{tarefa_id}/manutencao/enviar-compras")
def tarefa_manutencao_enviar_compras(
    tarefa_id: int,
    item_ids: List[int] = Form(...),
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db)
):
    tarefa = db.query(Tarefa).filter(Tarefa.id == tarefa_id).first()
    if not tarefa:
        raise HTTPException(status_code=404)
    manutencao = obter_ou_criar_manutencao_da_tarefa(db, tarefa, usuario)
    itens = db.query(ManutencaoItem).filter(ManutencaoItem.id.in_(item_ids), ManutencaoItem.manutencao_id == manutencao.id, ManutencaoItem.aprovado == 1).all()
    if not itens:
        raise HTTPException(status_code=400, detail="Selecione pelo menos um item aprovado para compras.")

    nomes = []
    for item in itens:
        item.comprar = 1
        item.externo = 0
        nomes.append(item.nome)
        ja_existe = db.query(Compra).filter(Compra.manutencao_id == manutencao.id, Compra.produto == item.nome).first()
        if not ja_existe:
            db.add(Compra(manutencao_id=manutencao.id, produto=item.nome, quantidade=item.quantidade, valor=item.valor_unitario, responsavel=usuario.nome, observacao="Gerado pela manutenção"))

    manutencao.status = "Pendente Compras"
    tarefa.status = "Compras"
    registrar_historico(db, tarefa.id, "Enviado para Compras", f"Itens: {', '.join(nomes)} · Status: Compras", usuario, tipo="compra")
    db.commit()
    return RedirectResponse(f"/organiza/tarefas/{tarefa_id}/manutencao", status_code=303)


@app.post("/organiza/tarefas/{tarefa_id}/manutencao/enviar-externo")
def tarefa_manutencao_enviar_externo(
    tarefa_id: int,
    item_ids: List[int] = Form(...),
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db)
):
    tarefa = db.query(Tarefa).filter(Tarefa.id == tarefa_id).first()
    if not tarefa:
        raise HTTPException(status_code=404)
    manutencao = obter_ou_criar_manutencao_da_tarefa(db, tarefa, usuario)
    itens = db.query(ManutencaoItem).filter(ManutencaoItem.id.in_(item_ids), ManutencaoItem.manutencao_id == manutencao.id, ManutencaoItem.aprovado == 1).all()
    if not itens:
        raise HTTPException(status_code=400, detail="Selecione pelo menos um item aprovado para externo.")

    nomes = []
    for item in itens:
        item.externo = 1
        item.comprar = 0
        nomes.append(item.nome)

    status_anterior = tarefa.status
    manutencao.status = "Pendente Externo"
    tarefa.status = "Externo"
    descricao = "Tenho equipamento para manutenção a ser enviado ao externo: " + ", ".join(nomes)
    criar_chamado_se_necessario(db, tarefa, status_anterior, usuario, descricao)
    registrar_historico(db, tarefa.id, "Enviado para Externo", f"Itens: {', '.join(nomes)} · Status: Externo", usuario, tipo="externo")
    db.commit()
    return RedirectResponse(f"/organiza/tarefas/{tarefa_id}/manutencao", status_code=303)


@app.post("/organiza/tarefas/{tarefa_id}/manutencao/pagamento")
def tarefa_manutencao_pagamento(
    tarefa_id: int,
    item_ids: List[int] = Form(...),
    parcela_banco: List[str] = Form([]),
    parcela_valor: List[str] = Form([]),
    parcela_forma: List[str] = Form([]),
    parcela_data: List[str] = Form([]),
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db)
):
    tarefa = db.query(Tarefa).filter(Tarefa.id == tarefa_id).first()
    if not tarefa:
        raise HTTPException(status_code=404)

    manutencao = obter_ou_criar_manutencao_da_tarefa(db, tarefa, usuario)
    itens = db.query(ManutencaoItem).filter(ManutencaoItem.id.in_(item_ids), ManutencaoItem.manutencao_id == manutencao.id, ManutencaoItem.aprovado == 1).all()
    if not itens:
        raise HTTPException(status_code=400, detail="Selecione pelo menos um item aprovado para financeiro.")

    nomes = [item.nome for item in itens]
    total_itens_num = sum([_numero_decimal(item.valor_total or item.valor_unitario or "0") for item in itens])

    parcelas = []
    for indice, banco in enumerate(parcela_banco or []):
        valor = parcela_valor[indice] if indice < len(parcela_valor or []) else ""
        forma = parcela_forma[indice] if indice < len(parcela_forma or []) else ""
        data_venc = parcela_data[indice] if indice < len(parcela_data or []) else ""
        if not valor:
            continue
        valor_num = _numero_decimal(valor)
        if valor_num <= 0:
            continue
        parcelas.append({
            "banco": banco.strip() if banco else "",
            "valor": valor,
            "forma": forma.strip() if forma else "",
            "vencimento": parse_data(data_venc) or date.today(),
        })

    if not parcelas:
        raise HTTPException(status_code=400, detail="Informe pelo menos uma parcela para o financeiro.")

    total_parcelas = sum(_numero_decimal(p["valor"]) for p in parcelas)
    if abs(total_parcelas - total_itens_num) > 0.01:
        raise HTTPException(status_code=400, detail="A soma das parcelas do financeiro precisa bater com o total dos itens selecionados.")

    for item in itens:
        item.aprovado = 1
        item.comprar = 0
        item.externo = 0

    tarefa.status = "Financeiro"
    manutencao.status = "Pendente Financeiro"
    descricao_base = f"Recebimento manutenção #{tarefa.id} - {manutencao.nome_cliente} - {', '.join(nomes)}"

    for indice, parcela in enumerate(parcelas, start=1):
        descricao = descricao_base
        if len(parcelas) > 1:
            descricao += f" · Parcela {indice}/{len(parcelas)}"
        db.add(ContaFinanceira(
            origem="manutencao",
            origem_id=manutencao.id,
            tipo="receber",
            pessoa=manutencao.nome_cliente or "Cliente",
            categoria="Manutenção",
            banco=parcela["banco"] or "Santander",
            descricao=descricao,
            valor=_moeda_br(_numero_decimal(parcela["valor"])),
            vencimento=parcela["vencimento"],
            forma=parcela["forma"],
            status="A receber",
            observacao=f"Itens: {', '.join(nomes)}",
        ))

    registrar_historico(db, tarefa.id, "Enviado para Financeiro", f"Itens: {', '.join(nomes)} · {len(parcelas)} lançamento(s) · Status: Financeiro", usuario, tipo="financeiro")

    db.commit()
    return RedirectResponse(f"/organiza/tarefas/{tarefa_id}/manutencao", status_code=303)



@app.post("/organiza/tarefas/{tarefa_id}/manutencao/compras-concluir")
def tarefa_manutencao_compras_concluir(tarefa_id: int, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    tarefa = db.query(Tarefa).filter(Tarefa.id == tarefa_id).first()
    if not tarefa:
        raise HTTPException(status_code=404)
    manutencao = obter_ou_criar_manutencao_da_tarefa(db, tarefa, usuario)
    tarefa.status = "Pendente"
    manutencao.status = "Pendente"
    registrar_historico(db, tarefa.id, "Compras concluíram", "Status: Pendente", usuario, tipo="compra")
    db.commit()
    return RedirectResponse(f"/organiza/tarefas/{tarefa_id}/manutencao", status_code=303)


@app.post("/organiza/tarefas/{tarefa_id}/manutencao/financeiro-concluir")
def tarefa_manutencao_financeiro_concluir(tarefa_id: int, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    tarefa = db.query(Tarefa).filter(Tarefa.id == tarefa_id).first()
    if not tarefa:
        raise HTTPException(status_code=404)
    manutencao = obter_ou_criar_manutencao_da_tarefa(db, tarefa, usuario)
    tarefa.status = "Pendente"
    manutencao.status = "Pendente"
    registrar_historico(db, tarefa.id, "Financeiro concluiu", "Status: Pendente", usuario, tipo="financeiro")
    db.commit()
    return RedirectResponse(f"/organiza/tarefas/{tarefa_id}/manutencao", status_code=303)


@app.post("/organiza/tarefas/{tarefa_id}/manutencao/externo-concluir")
def tarefa_manutencao_externo_concluir(tarefa_id: int, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    tarefa = db.query(Tarefa).filter(Tarefa.id == tarefa_id).first()
    if not tarefa:
        raise HTTPException(status_code=404)
    manutencao = obter_ou_criar_manutencao_da_tarefa(db, tarefa, usuario)
    tarefa.status = "Pendente"
    manutencao.status = "Pendente"
    registrar_historico(db, tarefa.id, "Retornou do Externo", "Status: Pendente", usuario, tipo="externo")
    db.commit()
    return RedirectResponse(f"/organiza/tarefas/{tarefa_id}/manutencao", status_code=303)


@app.post("/organiza/tarefas/{tarefa_id}/manutencao/orcamento")
def tarefa_manutencao_enviar_orcamento(tarefa_id: int, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    tarefa = db.query(Tarefa).filter(Tarefa.id == tarefa_id).first()
    if not tarefa:
        raise HTTPException(status_code=404)
    manutencao = obter_ou_criar_manutencao_da_tarefa(db, tarefa, usuario)
    anterior = tarefa.status
    tarefa.status = "Aguardando Cliente"
    manutencao.status = "Orçamento Cliente"
    manutencao.data_orcamento = manutencao.data_orcamento or date.today()
    manutencao.hora_orcamento = manutencao.hora_orcamento or datetime.now().strftime("%H:%M")
    registrar_historico(db, tarefa.id, "Orçamento enviado ao cliente", "Status: Aguardando Cliente (Orçamento)", usuario, tipo="manutencao")
    db.commit()
    return RedirectResponse(f"/organiza/tarefas/{tarefa_id}/manutencao", status_code=303)


@app.post("/organiza/tarefas/{tarefa_id}/manutencao/aprovar-orcamento")
def tarefa_manutencao_aprovar_orcamento(
    tarefa_id: int,
    item_ids: List[int] = Form(...),
    decisao: str = Form("aprovado"),
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db)
):
    tarefa = db.query(Tarefa).filter(Tarefa.id == tarefa_id).first()
    if not tarefa:
        raise HTTPException(status_code=404)
    manutencao = obter_ou_criar_manutencao_da_tarefa(db, tarefa, usuario)
    itens = db.query(ManutencaoItem).filter(ManutencaoItem.id.in_(item_ids), ManutencaoItem.manutencao_id == manutencao.id).all()
    if not itens:
        raise HTTPException(status_code=400, detail="Selecione pelo menos um item do orçamento.")
    if decisao == "aprovado":
        valor_decisao = 1
    elif decisao == "nao_aprovado":
        valor_decisao = -1
    else:
        valor_decisao = 0

    nomes = []
    for item in itens:
        item.aprovado = valor_decisao
        if valor_decisao != 1:
            item.comprar = 0
            item.externo = 0
        nomes.append(item.nome)

    tarefa.status = "Pendente"
    manutencao.status = "Pendente"
    # A decisão por item não entra na timeline operacional da OS.
    db.commit()
    return RedirectResponse(f"/organiza/tarefas/{tarefa_id}/manutencao", status_code=303)


@app.post("/organiza/tarefas/{tarefa_id}/manutencao/concluir")
def tarefa_manutencao_concluir(tarefa_id: int, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    tarefa = db.query(Tarefa).filter(Tarefa.id == tarefa_id).first()
    if not tarefa:
        raise HTTPException(status_code=404)
    manutencao = obter_ou_criar_manutencao_da_tarefa(db, tarefa, usuario)
    anterior = tarefa.status
    tarefa.status = "Aguardando Cliente"
    manutencao.status = "Aguardando Cliente Buscar"
    manutencao.data_conclusao = manutencao.data_conclusao or date.today()
    registrar_historico(db, tarefa.id, "Manutenção concluída", "Status: Aguardando Cliente (Retirada)", usuario, tipo="manutencao")
    registrar_historico(db, tarefa.id, "Aguardando retirada do cliente", "Responsabilidade: Cliente", usuario, tipo="manutencao")
    db.commit()
    return RedirectResponse(f"/organiza/tarefas/{tarefa_id}/manutencao?copiar=retirada", status_code=303)


@app.post("/organiza/tarefas/{tarefa_id}/manutencao/retirar")
def tarefa_manutencao_retirar(tarefa_id: int, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    tarefa = db.query(Tarefa).filter(Tarefa.id == tarefa_id).first()
    if not tarefa:
        raise HTTPException(status_code=404)
    manutencao = obter_ou_criar_manutencao_da_tarefa(db, tarefa, usuario)
    anterior = tarefa.status
    tarefa.status = "Encerrado"
    manutencao.status = "Entregue"
    manutencao.data_retirada = manutencao.data_retirada or date.today()
    registrar_historico(db, tarefa.id, "Equipamento retirado / OS encerrada", "Status final: Entregue", usuario, tipo="manutencao")
    db.commit()
    return RedirectResponse("/organiza", status_code=303)


@app.post("/organiza/tarefas/{tarefa_id}/manutencao/cancelar")
def tarefa_manutencao_cancelar(tarefa_id: int, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    tarefa = db.query(Tarefa).filter(Tarefa.id == tarefa_id).first()
    if not tarefa:
        raise HTTPException(status_code=404)
    manutencao = obter_ou_criar_manutencao_da_tarefa(db, tarefa, usuario)
    anterior = tarefa.status
    tarefa.status = "Cancelado"
    manutencao.status = "Cancelada"
    registrar_historico(db, tarefa.id, "OS cancelada", "Status: Cancelado", usuario, tipo="manutencao")
    db.commit()
    return RedirectResponse("/organiza", status_code=303)


@app.post("/organiza/tarefas/{tarefa_id}/manutencao/mensagem")
def tarefa_manutencao_mensagem(tarefa_id: int, texto: str = Form(...), usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    tarefa = db.query(Tarefa).filter(Tarefa.id == tarefa_id).first()
    if not tarefa:
        raise HTTPException(status_code=404)
    if not texto.strip():
        raise HTTPException(status_code=400, detail="A mensagem não pode ficar vazia.")
    db.add(Mensagem(tarefa_id=tarefa.id, cliente_id=tarefa.cliente_id, canal="WhatsApp", direcao="saida", texto=texto.strip(), status="rascunho", enviado_por=usuario.nome))
    registrar_historico(db, tarefa.id, "Mensagem registrada", texto.strip(), usuario, tipo="mensagem")
    db.commit()
    return RedirectResponse(f"/organiza/tarefas/{tarefa_id}/manutencao", status_code=303)


@app.post("/organiza/tarefas/{tarefa_id}/manutencao/compra")
def tarefa_manutencao_compra(tarefa_id: int, produto: str = Form(...), quantidade: str = Form("1"), observacao: str = Form(""), usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    tarefa = db.query(Tarefa).filter(Tarefa.id == tarefa_id).first()
    if not tarefa:
        raise HTTPException(status_code=404)
    manutencao = obter_ou_criar_manutencao_da_tarefa(db, tarefa, usuario)
    manutencao.status = "Aguardando Peça"
    status_anterior = tarefa.status
    tarefa.status = "Pendente"
    db.add(Compra(manutencao_id=manutencao.id, produto=produto.strip(), quantidade=_valor(quantidade), responsavel=usuario.nome, observacao=_valor(observacao)))
    registrar_historico(db, tarefa.id, "Compra solicitada pela manutenção", produto.strip(), usuario, tipo="compra")
    registrar_historico(db, tarefa.id, f"Status da tarefa alterado para {tarefa.status}", f"Antes: {status_anterior}", usuario, tipo="status")
    db.commit()
    return RedirectResponse(f"/organiza/tarefas/{tarefa_id}/manutencao", status_code=303)


@app.get("/organiza/manutencoes", response_class=HTMLResponse)
def manutencoes(request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    itens = db.query(Manutencao).order_by(Manutencao.criado_em.desc()).all()
    return templates.TemplateResponse("organiza/manutencoes.html", {
        "request": request, "usuario": usuario, "manutencoes": itens, "titulo": "Manutenção | Organiza",
        "status_manutencao": STATUS_MANUTENCAO,
    })


@app.get("/organiza/manutencoes/nova", response_class=HTMLResponse)
def manutencao_nova(request: Request, usuario: Usuario = Depends(usuario_logado)):
    return templates.TemplateResponse("organiza/manutencao_form.html", {
        "request": request, "usuario": usuario, "manutencao": None, "titulo": "Nova manutenção | Organiza",
        "status_manutencao": STATUS_MANUTENCAO,
    })


@app.post("/organiza/manutencoes/nova")
def manutencao_criar(
    nome_cliente: str = Form(...), telefone: str = Form(""), equipamento: str = Form(""),
    problema: str = Form(...), responsavel: str = Form("Luiz"), observacao: str = Form(""),
    usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)
):
    numero = limpar_telefone(telefone)
    cliente = db.query(Cliente).filter(Cliente.telefone == numero).first() if numero else None
    if numero and not cliente:
        cliente = Cliente(nome=nome_cliente.strip(), telefone=numero)
        db.add(cliente)
        db.flush()
    db.add(Manutencao(
        cliente_id=cliente.id if cliente else None,
        nome_cliente=nome_cliente.strip(),
        telefone=numero,
        equipamento=_valor(equipamento),
        problema=problema.strip(),
        responsavel=_valor(responsavel) or usuario.nome,
        observacao=_valor(observacao),
    ))
    db.commit()
    return RedirectResponse("/organiza/manutencoes", status_code=303)


@app.get("/organiza/manutencoes/{manutencao_id}/editar", response_class=HTMLResponse)
def manutencao_editar(manutencao_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    item = db.query(Manutencao).filter(Manutencao.id == manutencao_id).first()
    if not item: raise HTTPException(status_code=404)
    return templates.TemplateResponse("organiza/manutencao_form.html", {
        "request": request, "usuario": usuario, "manutencao": item, "titulo": "Editar manutenção | Organiza",
        "status_manutencao": STATUS_MANUTENCAO,
    })


@app.post("/organiza/manutencoes/{manutencao_id}/editar")
def manutencao_salvar(
    manutencao_id: int, nome_cliente: str = Form(...), telefone: str = Form(""), equipamento: str = Form(""),
    problema: str = Form(...), status: str = Form("Recebida"), valor_orcamento: str = Form(""),
    forma_pagamento: str = Form(""), data_conclusao: str = Form(""), data_retirada: str = Form(""),
    observacao: str = Form(""), usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)
):
    item = db.query(Manutencao).filter(Manutencao.id == manutencao_id).first()
    if not item: raise HTTPException(status_code=404)
    item.nome_cliente = nome_cliente.strip()
    item.telefone = limpar_telefone(telefone)
    item.equipamento = _valor(equipamento)
    item.problema = problema.strip()
    item.status = status if status in STATUS_MANUTENCAO else item.status
    item.valor_orcamento = _valor(valor_orcamento)
    item.forma_pagamento = _valor(forma_pagamento)
    item.data_conclusao = parse_data(data_conclusao)
    item.data_retirada = parse_data(data_retirada)
    item.observacao = _valor(observacao)
    if item.status == "Aguardando Financeiro" and item.valor_orcamento:
        existe = db.query(ContaFinanceira).filter(ContaFinanceira.origem == "Manutenção", ContaFinanceira.origem_id == item.id).first()
        if not existe:
            db.add(ContaFinanceira(
                tipo="receber", origem="Manutenção", origem_id=item.id, pessoa=item.nome_cliente,
                categoria="Manutenção", descricao=f"Manutenção #{item.id} - {item.equipamento or item.problema[:40]}",
                valor=item.valor_orcamento, status="A receber", observacao=item.forma_pagamento,
            ))
    db.commit()
    return RedirectResponse("/organiza/manutencoes", status_code=303)


@app.post("/organiza/manutencoes/{manutencao_id}/compra")
def manutencao_gerar_compra(
    manutencao_id: int, produto: str = Form(...), quantidade: str = Form("1"), observacao: str = Form(""),
    usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)
):
    item = db.query(Manutencao).filter(Manutencao.id == manutencao_id).first()
    if not item: raise HTTPException(status_code=404)
    item.status = "Aguardando Peça"
    db.add(Compra(manutencao_id=item.id, produto=produto.strip(), quantidade=_valor(quantidade), responsavel=usuario.nome, observacao=_valor(observacao)))
    db.commit()
    return RedirectResponse("/organiza/compras", status_code=303)


@app.get("/organiza/compras", response_class=HTMLResponse)
def compras(request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    exigir_permissao(usuario, "pode_acessar_compras")
    itens = db.query(Compra).order_by(Compra.criado_em.desc()).all()
    return templates.TemplateResponse("organiza/compras.html", {
        "request": request, "usuario": usuario, "compras": itens, "titulo": "Compras | Organiza",
        "status_compra": STATUS_COMPRA,
    })


@app.get("/organiza/compras/nova", response_class=HTMLResponse)
def compra_nova(request: Request, usuario: Usuario = Depends(usuario_logado)):
    exigir_permissao(usuario, "pode_acessar_compras")
    return templates.TemplateResponse("organiza/compra_form.html", {"request": request, "usuario": usuario, "compra": None, "titulo": "Nova compra | Organiza", "status_compra": STATUS_COMPRA})


@app.post("/organiza/compras/nova")
def compra_criar(produto: str = Form(...), quantidade: str = Form("1"), fornecedor: str = Form(""), valor: str = Form(""), previsao_entrega: str = Form(""), observacao: str = Form(""), usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    exigir_permissao(usuario, "pode_acessar_compras")
    db.add(Compra(produto=produto.strip(), quantidade=_valor(quantidade), fornecedor=_valor(fornecedor), valor=_valor(valor), previsao_entrega=parse_data(previsao_entrega), responsavel=usuario.nome, observacao=_valor(observacao)))
    db.commit()
    return RedirectResponse("/organiza/compras", status_code=303)


@app.get("/organiza/compras/{compra_id}/editar", response_class=HTMLResponse)
def compra_editar(compra_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    item = db.query(Compra).filter(Compra.id == compra_id).first()
    if not item: raise HTTPException(status_code=404)
    return templates.TemplateResponse("organiza/compra_form.html", {"request": request, "usuario": usuario, "compra": item, "titulo": "Editar compra | Organiza", "status_compra": STATUS_COMPRA})


@app.post("/organiza/compras/{compra_id}/editar")
def compra_salvar(compra_id: int, produto: str = Form(...), quantidade: str = Form("1"), fornecedor: str = Form(""), valor: str = Form(""), status: str = Form("Solicitada"), previsao_entrega: str = Form(""), data_recebimento: str = Form(""), observacao: str = Form(""), usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    item = db.query(Compra).filter(Compra.id == compra_id).first()
    if not item: raise HTTPException(status_code=404)
    item.produto = produto.strip()
    item.quantidade = _valor(quantidade)
    item.fornecedor = _valor(fornecedor)
    item.valor = _valor(valor)
    # Compra vinda da manutenção: ao salvar, Compras já concluiu a etapa.
    # A partir daqui o pagamento segue o fluxo normal do Financeiro como conta a pagar.
    if item.manutencao_id:
        item.status = "Recebida"
        item.data_recebimento = parse_data(data_recebimento) or date.today()
    else:
        item.status = status if status in STATUS_COMPRA else item.status
    item.previsao_entrega = parse_data(previsao_entrega)
    item.observacao = _valor(observacao)

    if item.valor:
        dados_pagamento = _valor(observacao)
        descricao_financeira = f"Compra #{item.id} - {item.produto}"
        if item.manutencao_id:
            descricao_financeira = f"Compra manutenção #{item.manutencao_id} - {item.produto}"
        if dados_pagamento:
            descricao_financeira = f"{descricao_financeira}\nDados bancários/PIX: {dados_pagamento}"

        conta_pagar = db.query(ContaFinanceira).filter(
            ContaFinanceira.origem == "Compra",
            ContaFinanceira.origem_id == item.id
        ).first()
        if conta_pagar:
            conta_pagar.pessoa = item.fornecedor or "Fornecedor"
            conta_pagar.categoria = "Compras"
            conta_pagar.descricao = descricao_financeira
            conta_pagar.valor = item.valor
            conta_pagar.vencimento = item.previsao_entrega
            conta_pagar.observacao = dados_pagamento
            if conta_pagar.status in {"Recebido", "Pago", "Cancelado"}:
                conta_pagar.status = conta_pagar.status
            else:
                conta_pagar.status = "Pendente"
        else:
            db.add(ContaFinanceira(
                tipo="pagar",
                origem="Compra",
                origem_id=item.id,
                pessoa=item.fornecedor or "Fornecedor",
                categoria="Compras",
                descricao=descricao_financeira,
                valor=item.valor,
                vencimento=item.previsao_entrega,
                status="Pendente",
                observacao=dados_pagamento,
            ))

    if item.status == "Recebida" and item.manutencao:
        item.manutencao.status = "Pendente"
        if item.manutencao.tarefa:
            anterior = item.manutencao.tarefa.status
            item.manutencao.tarefa.status = "Pendente"
            registrar_historico(db, item.manutencao.tarefa.id, "Compras concluíram", "OS voltou para Pendente.", usuario, tipo="compra")
            if anterior != "Pendente":
                registrar_historico(db, item.manutencao.tarefa.id, "Status da tarefa alterado para Pendente", f"Antes: {anterior}", usuario, tipo="status")
    db.commit()
    return RedirectResponse("/organiza/compras", status_code=303)



def atualizar_os_manutencao_apos_financeiro(db: Session, manutencao_id: int, usuario: Optional[Usuario] = None):
    """Volta a OS para Pendente quando todos os recebimentos financeiros da manutenção forem recebidos."""
    if not manutencao_id:
        return False

    manut = db.query(Manutencao).filter(Manutencao.id == manutencao_id).first()
    if not manut:
        return False

    total_receber = db.query(ContaFinanceira).filter(
        ContaFinanceira.origem == "manutencao",
        ContaFinanceira.origem_id == manutencao_id,
        ContaFinanceira.tipo == "receber",
        ContaFinanceira.status != "Cancelado",
    ).count()

    if total_receber == 0:
        return False

    pendentes = db.query(ContaFinanceira).filter(
        ContaFinanceira.origem == "manutencao",
        ContaFinanceira.origem_id == manutencao_id,
        ContaFinanceira.tipo == "receber",
        ContaFinanceira.status.notin_(["Recebido", "Cancelado"]),
    ).count()

    if pendentes == 0:
        alterou = False
        if manut.status != "Pendente":
            manut.status = "Pendente"
            alterou = True

        if manut.tarefa and manut.tarefa.status != "Pendente":
            manut.tarefa.status = "Pendente"
            alterou = True

        if alterou and usuario and manut.tarefa:
            registrar_historico(
                db,
                manut.tarefa.id,
                "Financeiro concluiu",
                "Todos os recebimentos foram confirmados. Status: Pendente",
                usuario,
                tipo="financeiro",
            )
        return alterou

    return False


@app.get("/organiza/financeiro", response_class=HTMLResponse)
def financeiro(request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    exigir_permissao(usuario, "pode_acessar_financeiro")

    manutencoes_financeiras = db.query(ContaFinanceira.origem_id).filter(
        ContaFinanceira.origem == "manutencao",
        ContaFinanceira.origem_id.isnot(None),
        ContaFinanceira.tipo == "receber",
    ).distinct().all()
    for linha in manutencoes_financeiras:
        atualizar_os_manutencao_apos_financeiro(db, linha[0], usuario)
    db.commit()

    contas = db.query(ContaFinanceira).order_by(ContaFinanceira.criado_em.desc()).all()
    return templates.TemplateResponse("organiza/financeiro.html", {"request": request, "usuario": usuario, "contas": contas, "titulo": "Financeiro | Organiza"})


@app.get("/organiza/financeiro/nova", response_class=HTMLResponse)
def financeiro_nova(request: Request, tipo: str = "pagar", usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    exigir_permissao(usuario, "pode_acessar_financeiro")
    bancos_lista = db.query(BancoConta).filter(BancoConta.ativo == 1).order_by(BancoConta.nome.asc()).all() if "BancoConta" in globals() else []
    return templates.TemplateResponse("organiza/financeiro_form.html", {"request": request, "usuario": usuario, "conta": None, "tipo": tipo if tipo in TIPOS_CONTA else "pagar", "bancos": bancos_lista, "hoje": date.today(), "titulo": "Nova conta | Organiza"})


@app.post("/organiza/financeiro/nova")
def financeiro_criar(tipo: str = Form(...), pessoa: str = Form(...), categoria: str = Form(""), descricao: str = Form(...), valor: str = Form(...), vencimento: str = Form(""), banco: str = Form(""), forma: str = Form(""), observacao: str = Form(""), usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    exigir_permissao(usuario, "pode_acessar_financeiro")
    status = "A receber" if tipo == "receber" else "Pendente"
    db.add(ContaFinanceira(tipo=tipo, pessoa=pessoa.strip(), categoria=_valor(categoria), descricao=descricao.strip(), valor=_valor(valor), vencimento=parse_data(vencimento), banco=_valor(banco), forma=_valor(forma), status=status, observacao=_valor(observacao)))
    db.commit()
    return RedirectResponse("/organiza/financeiro", status_code=303)


@app.get("/organiza/financeiro/{conta_id}/editar", response_class=HTMLResponse)
def financeiro_editar(conta_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    conta = db.query(ContaFinanceira).filter(ContaFinanceira.id == conta_id).first()
    if not conta: raise HTTPException(status_code=404)
    bancos_lista = db.query(BancoConta).filter(BancoConta.ativo == 1).order_by(BancoConta.nome.asc()).all()
    return templates.TemplateResponse("organiza/financeiro_form.html", {"request": request, "usuario": usuario, "conta": conta, "tipo": conta.tipo, "bancos": bancos_lista, "hoje": date.today(), "titulo": "Editar conta | Organiza"})


@app.post("/organiza/financeiro/{conta_id}/editar")
def financeiro_salvar(conta_id: int, pessoa: str = Form(...), categoria: str = Form(""), descricao: str = Form(...), valor: str = Form(...), vencimento: str = Form(""), banco: str = Form(""), forma: str = Form(""), status: str = Form(""), pago_em: str = Form(""), observacao: str = Form(""), movimento_banco: List[str] = Form([]), movimento_valor: List[str] = Form([]), movimento_data: List[str] = Form([]), movimento_forma: List[str] = Form([]), usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    conta = db.query(ContaFinanceira).filter(ContaFinanceira.id == conta_id).first()
    if not conta: raise HTTPException(status_code=404)
    conta.pessoa = pessoa.strip()
    conta.categoria = _valor(categoria)
    conta.descricao = descricao.strip()
    conta.valor = _valor(valor)
    conta.vencimento = parse_data(vencimento)
    conta.banco = _valor(banco)
    conta.forma = _valor(forma)
    conta.status = status or conta.status
    conta.pago_em = parse_data(pago_em)
    conta.observacao = _valor(observacao)
    if conta.status in {"Recebido", "Pago"}:
        # Financeiro da manutenção pode ser recebido em um banco ou dividido em vários bancos.
        db.query(BancoMovimento).filter(BancoMovimento.conta_id == conta.id).delete()
        bancos_informados = [b.strip() for b in (movimento_banco or [])]
        valores_informados = [v.strip() for v in (movimento_valor or [])]
        datas_informadas = [d.strip() for d in (movimento_data or [])]
        formas_informadas = [f.strip() for f in (movimento_forma or [])]
        total_movimentos = 0.0

        for indice, banco_mov in enumerate(bancos_informados):
            valor_mov = valores_informados[indice] if indice < len(valores_informados) else ""
            if not banco_mov or not valor_mov:
                continue
            total_movimentos += _numero_decimal(valor_mov)
            data_mov = datas_informadas[indice] if indice < len(datas_informadas) else ""
            forma_mov = formas_informadas[indice] if indice < len(formas_informadas) else ""
            db.add(BancoMovimento(
                conta_id=conta.id,
                tipo="entrada" if conta.tipo == "receber" else "saida",
                banco=banco_mov,
                categoria=conta.categoria,
                descricao=f"{conta.descricao}" + (f" · {forma_mov}" if forma_mov else ""),
                valor=_valor(valor_mov),
                data=parse_data(data_mov) or conta.pago_em or date.today(),
                conciliado=1,
            ))

        if total_movimentos == 0:
            _criar_lancamento_banco(db, conta)

        if conta.origem == "manutencao" and conta.origem_id:
            atualizar_os_manutencao_apos_financeiro(db, conta.origem_id, usuario)
    db.commit()
    return RedirectResponse("/organiza/financeiro", status_code=303)



@app.post("/organiza/financeiro/{conta_id}/receber")
def financeiro_receber_conta(conta_id: int, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    exigir_permissao(usuario, "pode_acessar_financeiro")
    conta = db.query(ContaFinanceira).filter(ContaFinanceira.id == conta_id).first()
    if not conta:
        raise HTTPException(status_code=404)

    conta.status = "Recebido" if conta.tipo == "receber" else "Pago"
    conta.pago_em = conta.pago_em or date.today()
    if not conta.movimentos:
        _criar_lancamento_banco(db, conta)

    if conta.origem == "manutencao" and conta.origem_id:
        atualizar_os_manutencao_apos_financeiro(db, conta.origem_id, usuario)

    db.commit()
    return RedirectResponse("/organiza/financeiro", status_code=303)


@app.get("/organiza/bancos", response_class=HTMLResponse)
def bancos(request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    exigir_permissao(usuario, "pode_cadastrar_banco")
    bancos_lista = db.query(BancoConta).order_by(BancoConta.ativo.desc(), BancoConta.nome.asc()).all()
    return templates.TemplateResponse("organiza/bancos.html", {
        "request": request,
        "usuario": usuario,
        "bancos": bancos_lista,
        "titulo": "Cadastro de bancos | Organiza",
    })


@app.post("/organiza/bancos")
def bancos_salvar(
    nome: str = Form(...),
    saldo_inicial: str = Form("0,00"),
    ativo: str = Form("1"),
    banco_id: str = Form(""),
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db)
):
    exigir_permissao(usuario, "pode_cadastrar_banco")
    banco = None
    if banco_id:
        banco = db.query(BancoConta).filter(BancoConta.id == int(banco_id)).first()
    if not banco:
        banco = BancoConta()
        db.add(banco)
    banco.nome = nome.strip()
    banco.saldo_inicial = (saldo_inicial or "0,00").strip()
    banco.ativo = 1 if ativo == "1" else 0
    db.commit()
    return RedirectResponse("/organiza/bancos", status_code=303)


@app.post("/organiza/bancos/{banco_id}/alternar")
def bancos_alternar(banco_id: int, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    exigir_permissao(usuario, "pode_cadastrar_banco")
    banco = db.query(BancoConta).filter(BancoConta.id == banco_id).first()
    if not banco:
        raise HTTPException(status_code=404)
    banco.ativo = 0 if banco.ativo else 1
    db.commit()
    return RedirectResponse("/organiza/bancos", status_code=303)


@app.get("/organiza/banco", response_class=HTMLResponse)
def banco(request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    exigir_permissao(usuario, "pode_acessar_financeiro")
    movimentos = db.query(BancoMovimento).order_by(BancoMovimento.data.desc(), BancoMovimento.id.desc()).all()
    bancos_lista = db.query(BancoConta).order_by(BancoConta.ativo.desc(), BancoConta.nome.asc()).all()
    return templates.TemplateResponse("organiza/banco.html", {"request": request, "usuario": usuario, "movimentos": movimentos, "bancos": bancos_lista, "titulo": "Banco | Organiza"})


@app.post("/organiza/banco/{movimento_id}/conciliar")
def conciliar_banco(movimento_id: int, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    exigir_permissao(usuario, "pode_acessar_financeiro")
    movimento = db.query(BancoMovimento).filter(BancoMovimento.id == movimento_id).first()
    if not movimento:
        raise HTTPException(status_code=404)
    movimento.conciliado = 1
    # Quando Débora confirma o recebimento, a tarefa vinculada volta para Pendente.
    if "manutenção #" in movimento.descricao.lower():
        import re
        achado = re.search(r"#(\d+)", movimento.descricao)
        if achado:
            tarefa = db.query(Tarefa).filter(Tarefa.id == int(achado.group(1))).first()
            if tarefa:
                anterior = tarefa.status
                tarefa.status = "Pendente"
                manutencao = tarefa.manutencao
                if manutencao:
                    manutencao.status = "Pendente"
                registrar_historico(db, tarefa.id, "Pagamento conciliado pela Débora", f"Banco: {movimento.banco} - R$ {movimento.valor}", usuario, tipo="financeiro")
                if anterior != tarefa.status:
                    registrar_historico(db, tarefa.id, f"Status da tarefa alterado para {tarefa.status}", f"Antes: {anterior}", usuario, tipo="status")
    db.commit()
    return RedirectResponse("/organiza/banco", status_code=303)


