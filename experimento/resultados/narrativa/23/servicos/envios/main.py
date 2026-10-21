# arquivo: servicos/envios/main.py
"""Microsserviço de Envios."""
import copy
import os
import re
import threading
import uuid
from datetime import datetime
from urllib.parse import quote

import httpx
from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, StrictStr, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

TIMEOUT = 10.0
DATA_HORA_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
STATUS_ATUALIZACAO = {"COLETADO", "EM_TRANSITO", "SAIU_PARA_ENTREGA", "ENTREGUE", "FALHA_ENTREGA"}
TRANSICOES = {
    ("CRIADO", "COLETADO"),
    ("COLETADO", "EM_TRANSITO"),
    ("EM_TRANSITO", "EM_TRANSITO"),
    ("EM_TRANSITO", "SAIU_PARA_ENTREGA"),
    ("SAIU_PARA_ENTREGA", "ENTREGUE"),
    ("SAIU_PARA_ENTREGA", "FALHA_ENTREGA"),
    ("FALHA_ENTREGA", "SAIU_PARA_ENTREGA"),
}
MAX_FALHAS = 3

_lock = threading.RLock()
_envios: dict = {}
_contador = {"valor": 0}


class ErroAPI(Exception):
    def __init__(self, status: int, codigo: str, mensagem: str):
        self.status = status
        self.codigo = codigo
        self.mensagem = mensagem


def _erro(status: int, codigo: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request: Request, exc: ErroAPI):
    return _erro(exc.status, exc.codigo, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request: Request, exc: RequestValidationError):
    return _erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return _erro(404, "NAO_ENCONTRADO", "Recurso não encontrado.")
    if exc.status_code == 405:
        return _erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return _erro(exc.status_code, "ERRO", str(exc.detail))


def _validar_data_hora(v: str) -> str:
    if not DATA_HORA_RE.fullmatch(v):
        raise ValueError("data e hora mal formada")
    datetime.strptime(v, "%Y-%m-%dT%H:%M:%S")
    return v


def _nao_vazio(v: str) -> str:
    if not v.strip():
        raise ValueError("texto vazio")
    return v


class Destinatario(BaseModel):
    model_config = ConfigDict(extra="ignore")

    nome: StrictStr
    email: StrictStr

    @field_validator("nome")
    @classmethod
    def _v_nome(cls, v):
        return _nao_vazio(v)

    @field_validator("email")
    @classmethod
    def _v_email(cls, v):
        if not EMAIL_RE.match(v):
            raise ValueError("e-mail inválido")
        return v


class EnvioEntrada(BaseModel):
    model_config = ConfigDict(extra="ignore")

    pedido_id: StrictStr
    cotacao_id: StrictStr
    transportadora_id: StrictStr
    destinatario: Destinatario
    data_hora: StrictStr

    @field_validator("pedido_id", "cotacao_id", "transportadora_id")
    @classmethod
    def _v_textos(cls, v):
        return _nao_vazio(v)

    @field_validator("data_hora")
    @classmethod
    def _v_data_hora(cls, v):
        return _validar_data_hora(v)


class AtualizacaoEntrada(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: StrictStr
    data_hora: StrictStr
    local: StrictStr

    @field_validator("status")
    @classmethod
    def _v_status(cls, v):
        if v not in STATUS_ATUALIZACAO:
            raise ValueError("status inválido")
        return v

    @field_validator("data_hora")
    @classmethod
    def _v_data_hora(cls, v):
        return _validar_data_hora(v)

    @field_validator("local")
    @classmethod
    def _v_local(cls, v):
        return _nao_vazio(v)


class CancelamentoEntrada(BaseModel):
    model_config = ConfigDict(extra="ignore")

    data_hora: StrictStr

    @field_validator("data_hora")
    @classmethod
    def _v_data_hora(cls, v):
        return _validar_data_hora(v)


def _indisponivel(nome: str) -> ErroAPI:
    return ErroAPI(503, "SERVICO_INDISPONIVEL", f"Serviço de {nome} indisponível.")


def _buscar_cotacao(cotacao_id: str):
    base = os.environ.get("URL_COTACAO", "")
    try:
        resp = httpx.get(f"{base}/cotacoes/{quote(cotacao_id, safe='')}", timeout=TIMEOUT)
    except Exception:
        raise _indisponivel("cotação")
    if resp.status_code >= 500:
        raise _indisponivel("cotação")
    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        return None
    try:
        dados = resp.json()
    except Exception:
        raise _indisponivel("cotação")
    if not isinstance(dados, dict):
        raise _indisponivel("cotação")
    return dados


def _publicar(evento: dict) -> None:
    base = os.environ.get("URL_BARRAMENTO", "")
    try:
        resp = httpx.post(f"{base}/publicar", json=evento, timeout=TIMEOUT)
    except Exception:
        raise _indisponivel("barramento")
    if resp.status_code >= 300:
        raise _indisponivel("barramento")


def _novo_evento(tipo: str, ocorrido_em: str, dados: dict) -> dict:
    return {"id": str(uuid.uuid4()), "tipo": tipo, "ocorrido_em": ocorrido_em, "dados": dados}


def _evento_status(envio: dict, item: dict) -> dict:
    return _novo_evento(
        "envio.status_atualizado",
        item["data_hora"],
        {
            "envio_id": envio["id"],
            "codigo_rastreio": envio["codigo_rastreio"],
            "pedido_id": envio["pedido_id"],
            "status": item["status"],
            "data_hora": item["data_hora"],
            "local": item["local"],
            "destinatario_email": envio["destinatario"]["email"],
        },
    )


def _obter_ou_404(envio_id: str) -> dict:
    envio = _envios.get(envio_id)
    if envio is None:
        raise ErroAPI(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
    return envio


def _ultimo_data_hora(envio: dict) -> str:
    return envio["historico"][-1]["data_hora"]


def _parse_dh(v: str) -> datetime:
    return datetime.strptime(v, "%Y-%m-%dT%H:%M:%S")


@app.post("/admin/reset", status_code=204)
def reset():
    with _lock:
        _envios.clear()
        _contador["valor"] = 0
    return Response(status_code=204)


@app.post("/envios", status_code=201)
def criar_envio(entrada: EnvioEntrada):
    with _lock:
        for e in _envios.values():
            if e["pedido_id"] == entrada.pedido_id and e["status"] != "CANCELADO":
                raise ErroAPI(409, "ENVIO_DUPLICADO", "O pedido já possui envio ativo.")

        cotacao = _buscar_cotacao(entrada.cotacao_id)
        if cotacao is None:
            raise ErroAPI(422, "COTACAO_NAO_ENCONTRADA", "Cotação não encontrada.")

        data_envio = entrada.data_hora[:10]
        valida_ate = str(cotacao.get("valida_ate", ""))
        if data_envio > valida_ate:
            raise ErroAPI(422, "COTACAO_EXPIRADA", "A cotação expirou.")

        opcao = None
        for o in cotacao.get("opcoes", []) or []:
            if isinstance(o, dict) and o.get("transportadora_id") == entrada.transportadora_id:
                opcao = o
                break
        if opcao is None:
            raise ErroAPI(422, "TRANSPORTADORA_NAO_OFERECIDA", "Transportadora não oferecida na cotação.")

        numero = _contador["valor"] + 1
        envio = {
            "id": f"ENV-{numero:06d}",
            "codigo_rastreio": f"LG{numero:09d}BR",
            "pedido_id": entrada.pedido_id,
            "cotacao_id": entrada.cotacao_id,
            "transportadora_id": entrada.transportadora_id,
            "preco_centavos": opcao.get("preco_centavos"),
            "data_prevista_entrega": opcao.get("data_prevista_entrega"),
            "destinatario": {"nome": entrada.destinatario.nome, "email": entrada.destinatario.email},
            "status": "CRIADO",
            "tentativas_falhas": 0,
            "historico": [{"status": "CRIADO", "data_hora": entrada.data_hora, "local": None}],
        }
        evento = _novo_evento(
            "envio.criado",
            entrada.data_hora,
            {
                "envio_id": envio["id"],
                "codigo_rastreio": envio["codigo_rastreio"],
                "pedido_id": envio["pedido_id"],
                "transportadora_id": envio["transportadora_id"],
                "data_prevista_entrega": envio["data_prevista_entrega"],
                "destinatario_email": envio["destinatario"]["email"],
                "data_hora": entrada.data_hora,
            },
        )
        _publicar(evento)
        _contador["valor"] = numero
        _envios[envio["id"]] = envio
        return JSONResponse(status_code=201, content=copy.deepcopy(envio))


@app.get("/envios")
def listar_envios(pedido_id: str = Query(...)):
    with _lock:
        itens = [copy.deepcopy(e) for e in _envios.values() if e["pedido_id"] == pedido_id]
    itens.sort(key=lambda e: e["id"])
    return itens


@app.get("/envios/{envio_id}")
def obter_envio(envio_id: str):
    with _lock:
        return copy.deepcopy(_obter_ou_404(envio_id))


@app.post("/envios/{envio_id}/atualizacoes")
def atualizar_envio(envio_id: str, entrada: AtualizacaoEntrada):
    with _lock:
        atual = _obter_ou_404(envio_id)
        if (atual["status"], entrada.status) not in TRANSICOES:
            raise ErroAPI(409, "TRANSICAO_INVALIDA", "Transição de status inválida.")
        if _parse_dh(entrada.data_hora) < _parse_dh(_ultimo_data_hora(atual)):
            raise ErroAPI(422, "DATA_HORA_INVALIDA", "Data e hora anterior ao último registro.")

        novo = copy.deepcopy(atual)
        novos_itens = []
        item = {"status": entrada.status, "data_hora": entrada.data_hora, "local": entrada.local}
        novo["historico"].append(item)
        novo["status"] = entrada.status
        novos_itens.append(item)
        if entrada.status == "FALHA_ENTREGA":
            novo["tentativas_falhas"] += 1
            if novo["tentativas_falhas"] >= MAX_FALHAS:
                devolvido = {"status": "DEVOLVIDO", "data_hora": entrada.data_hora, "local": entrada.local}
                novo["historico"].append(devolvido)
                novo["status"] = "DEVOLVIDO"
                novos_itens.append(devolvido)

        for it in novos_itens:
            _publicar(_evento_status(novo, it))
        _envios[envio_id] = novo
        return copy.deepcopy(novo)


@app.post("/envios/{envio_id}/cancelar")
def cancelar_envio(envio_id: str, entrada: CancelamentoEntrada):
    with _lock:
        atual = _obter_ou_404(envio_id)
        if atual["status"] != "CRIADO":
            raise ErroAPI(409, "TRANSICAO_INVALIDA", "Só é possível cancelar envio no status CRIADO.")
        if _parse_dh(entrada.data_hora) < _parse_dh(_ultimo_data_hora(atual)):
            raise ErroAPI(422, "DATA_HORA_INVALIDA", "Data e hora anterior ao último registro.")

        novo = copy.deepcopy(atual)
        item = {"status": "CANCELADO", "data_hora": entrada.data_hora, "local": None}
        novo["historico"].append(item)
        novo["status"] = "CANCELADO"
        _publicar(_evento_status(novo, item))
        _envios[envio_id] = novo
        return copy.deepcopy(novo)
