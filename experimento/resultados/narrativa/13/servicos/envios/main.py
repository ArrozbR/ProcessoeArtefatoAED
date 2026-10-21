# arquivo: servicos/envios/main.py
import copy
import datetime
import os
import re
import threading
import uuid
from urllib.parse import quote

import httpx
from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, StrictStr, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException


class ErroAPI(Exception):
    def __init__(self, status: int, codigo: str, mensagem: str):
        super().__init__(mensagem)
        self.status = status
        self.codigo = codigo
        self.mensagem = mensagem


app = FastAPI()


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request: Request, exc: ErroAPI):
    return JSONResponse(status_code=exc.status, content={"erro": exc.codigo, "mensagem": exc.mensagem})


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={"erro": "DADOS_INVALIDOS", "mensagem": "Dados inválidos na requisição."})


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request: Request, exc: StarletteHTTPException):
    codigos = {404: "NAO_ENCONTRADO", 405: "METODO_NAO_PERMITIDO"}
    return JSONResponse(
        status_code=exc.status_code,
        content={"erro": codigos.get(exc.status_code, "ERRO"), "mensagem": str(exc.detail)},
    )


RE_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
RE_DATA_HORA = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}")

STATUS_ATUALIZACAO = {"COLETADO", "EM_TRANSITO", "SAIU_PARA_ENTREGA", "ENTREGUE", "FALHA_ENTREGA"}
TRANSICOES = {
    "CRIADO": {"COLETADO"},
    "COLETADO": {"EM_TRANSITO"},
    "EM_TRANSITO": {"EM_TRANSITO", "SAIU_PARA_ENTREGA"},
    "SAIU_PARA_ENTREGA": {"ENTREGUE", "FALHA_ENTREGA"},
    "FALHA_ENTREGA": {"SAIU_PARA_ENTREGA"},
}
MAX_FALHAS = 3

_lock = threading.RLock()
_envios: dict = {}
_estado = {"contador": 0}
_cliente_http = {"cliente": None}


def _cliente() -> httpx.Client:
    if _cliente_http["cliente"] is None:
        _cliente_http["cliente"] = httpx.Client(timeout=10.0)
    return _cliente_http["cliente"]


def _validar_data_hora(v: str) -> str:
    if not RE_DATA_HORA.fullmatch(v):
        raise ValueError("data_hora inválida")
    try:
        datetime.datetime.strptime(v, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        raise ValueError("data_hora inválida")
    return v


def _nao_vazio(v: str) -> str:
    if not v.strip():
        raise ValueError("texto vazio")
    return v


class Destinatario(BaseModel):
    nome: StrictStr
    email: StrictStr

    @field_validator("nome")
    @classmethod
    def _validar_nome(cls, v):
        return _nao_vazio(v)

    @field_validator("email")
    @classmethod
    def _validar_email(cls, v):
        if not RE_EMAIL.fullmatch(v):
            raise ValueError("email inválido")
        return v


class EnvioEntrada(BaseModel):
    pedido_id: StrictStr
    cotacao_id: StrictStr
    transportadora_id: StrictStr
    destinatario: Destinatario
    data_hora: StrictStr

    @field_validator("pedido_id", "cotacao_id", "transportadora_id")
    @classmethod
    def _validar_textos(cls, v):
        return _nao_vazio(v)

    @field_validator("data_hora")
    @classmethod
    def _validar_dh(cls, v):
        return _validar_data_hora(v)


class AtualizacaoEntrada(BaseModel):
    status: StrictStr
    data_hora: StrictStr
    local: StrictStr

    @field_validator("status")
    @classmethod
    def _validar_status(cls, v):
        if v not in STATUS_ATUALIZACAO:
            raise ValueError("status inválido")
        return v

    @field_validator("data_hora")
    @classmethod
    def _validar_dh(cls, v):
        return _validar_data_hora(v)

    @field_validator("local")
    @classmethod
    def _validar_local(cls, v):
        return _nao_vazio(v)


class CancelamentoEntrada(BaseModel):
    data_hora: StrictStr

    @field_validator("data_hora")
    @classmethod
    def _validar_dh(cls, v):
        return _validar_data_hora(v)


def _indisponivel(nome: str):
    return ErroAPI(503, "SERVICO_INDISPONIVEL", f"Serviço de {nome} indisponível.")


def _buscar_cotacao(cotacao_id: str) -> dict:
    base = os.environ.get("URL_COTACAO", "")
    try:
        resp = _cliente().get(f"{base}/cotacoes/{quote(cotacao_id, safe='')}")
    except Exception:
        raise _indisponivel("cotação")
    if resp.status_code >= 500:
        raise _indisponivel("cotação")
    if resp.status_code != 200:
        raise ErroAPI(422, "COTACAO_NAO_ENCONTRADA", "Cotação não encontrada.")
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
        resp = _cliente().post(f"{base}/publicar", json=evento)
    except Exception:
        raise _indisponivel("barramento")
    if resp.status_code >= 400:
        raise _indisponivel("barramento")


def _evento(tipo: str, ocorrido_em: str, dados: dict) -> dict:
    return {"id": str(uuid.uuid4()), "tipo": tipo, "ocorrido_em": ocorrido_em, "dados": dados}


def _evento_status(envio: dict, item: dict) -> dict:
    return _evento(
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


def _obter(envio_id: str) -> dict:
    e = _envios.get(envio_id)
    if e is None:
        raise ErroAPI(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
    return e


@app.post("/admin/reset", status_code=204)
def reset():
    with _lock:
        _envios.clear()
        _estado["contador"] = 0
    return Response(status_code=204)


@app.post("/envios", status_code=201)
def criar_envio(entrada: EnvioEntrada):
    with _lock:
        for e in _envios.values():
            if e["pedido_id"] == entrada.pedido_id and e["status"] != "CANCELADO":
                raise ErroAPI(409, "ENVIO_DUPLICADO", "O pedido já possui envio ativo.")

        cotacao = _buscar_cotacao(entrada.cotacao_id)
        valida_ate = cotacao.get("valida_ate")
        if isinstance(valida_ate, str) and entrada.data_hora[:10] > valida_ate:
            raise ErroAPI(422, "COTACAO_EXPIRADA", "A cotação expirou.")

        opcao = None
        for o in cotacao.get("opcoes") or []:
            if isinstance(o, dict) and o.get("transportadora_id") == entrada.transportadora_id:
                opcao = o
                break
        if opcao is None:
            raise ErroAPI(422, "TRANSPORTADORA_NAO_OFERECIDA", "Transportadora não oferecida na cotação.")

        numero = _estado["contador"] + 1
        envio_id = "ENV-%06d" % numero
        codigo = "LG%09dBR" % numero
        envio = {
            "id": envio_id,
            "codigo_rastreio": codigo,
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
        evento = _evento(
            "envio.criado",
            entrada.data_hora,
            {
                "envio_id": envio_id,
                "codigo_rastreio": codigo,
                "pedido_id": entrada.pedido_id,
                "transportadora_id": entrada.transportadora_id,
                "data_prevista_entrega": envio["data_prevista_entrega"],
                "destinatario_email": entrada.destinatario.email,
                "data_hora": entrada.data_hora,
            },
        )
        _publicar(evento)
        _estado["contador"] = numero
        _envios[envio_id] = envio
        return JSONResponse(status_code=201, content=copy.deepcopy(envio))


@app.get("/envios")
def listar_envios(pedido_id: str = Query(...)):
    with _lock:
        lista = [copy.deepcopy(_envios[k]) for k in sorted(_envios) if _envios[k]["pedido_id"] == pedido_id]
    return lista


@app.get("/envios/{envio_id}")
def obter_envio(envio_id: str):
    with _lock:
        return copy.deepcopy(_obter(envio_id))


@app.post("/envios/{envio_id}/atualizacoes")
def atualizar_envio(envio_id: str, entrada: AtualizacaoEntrada):
    with _lock:
        envio = _obter(envio_id)
        if entrada.status not in TRANSICOES.get(envio["status"], set()):
            raise ErroAPI(409, "TRANSICAO_INVALIDA", "Transição de status inválida.")
        if entrada.data_hora < envio["historico"][-1]["data_hora"]:
            raise ErroAPI(422, "DATA_HORA_INVALIDA", "Data e hora anteriores ao último registro.")

        novo = copy.deepcopy(envio)
        item = {"status": entrada.status, "data_hora": entrada.data_hora, "local": entrada.local}
        novo["historico"].append(item)
        novo["status"] = entrada.status
        eventos = [_evento_status(novo, item)]
        if entrada.status == "FALHA_ENTREGA":
            novo["tentativas_falhas"] += 1
            if novo["tentativas_falhas"] >= MAX_FALHAS:
                item_dev = {"status": "DEVOLVIDO", "data_hora": entrada.data_hora, "local": entrada.local}
                novo["historico"].append(item_dev)
                novo["status"] = "DEVOLVIDO"
                eventos.append(_evento_status(novo, item_dev))

        for ev in eventos:
            _publicar(ev)
        _envios[envio_id] = novo
        return copy.deepcopy(novo)


@app.post("/envios/{envio_id}/cancelar")
def cancelar_envio(envio_id: str, entrada: CancelamentoEntrada):
    with _lock:
        envio = _obter(envio_id)
        if envio["status"] != "CRIADO":
            raise ErroAPI(409, "TRANSICAO_INVALIDA", "Só é possível cancelar envio criado.")
        if entrada.data_hora < envio["historico"][-1]["data_hora"]:
            raise ErroAPI(422, "DATA_HORA_INVALIDA", "Data e hora anteriores ao último registro.")

        novo = copy.deepcopy(envio)
        item = {"status": "CANCELADO", "data_hora": entrada.data_hora, "local": None}
        novo["historico"].append(item)
        novo["status"] = "CANCELADO"
        _publicar(_evento_status(novo, item))
        _envios[envio_id] = novo
        return copy.deepcopy(novo)
