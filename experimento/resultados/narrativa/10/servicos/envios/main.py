# arquivo: servicos/envios/main.py
import copy
import os
import re
import threading
import uuid
from datetime import date, datetime
from typing import Literal
from urllib.parse import quote

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, StrictStr, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

RE_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
RE_DATA_HORA = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}")

TRANSICOES = {
    "CRIADO": {"COLETADO"},
    "COLETADO": {"EM_TRANSITO"},
    "EM_TRANSITO": {"EM_TRANSITO", "SAIU_PARA_ENTREGA"},
    "SAIU_PARA_ENTREGA": {"ENTREGUE", "FALHA_ENTREGA"},
    "FALHA_ENTREGA": {"SAIU_PARA_ENTREGA"},
}
MAX_FALHAS = 3


class ErroAPI(Exception):
    def __init__(self, status: int, codigo: str, mensagem: str):
        super().__init__(mensagem)
        self.status = status
        self.codigo = codigo
        self.mensagem = mensagem


app = FastAPI()

_lock = threading.RLock()
_estado = {"contador": 0, "envios": {}}


def _erro(status: int, codigo: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request: Request, exc: ErroAPI):
    return _erro(exc.status, exc.codigo, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request: Request, exc: RequestValidationError):
    return _erro(422, "DADOS_INVALIDOS", "Dados invalidos na requisicao.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return _erro(404, "NAO_ENCONTRADO", "Recurso nao encontrado.")
    if exc.status_code == 405:
        return _erro(405, "METODO_NAO_PERMITIDO", "Metodo nao permitido.")
    return _erro(exc.status_code, "ERRO", str(exc.detail))


def _validar_data_hora(v: str) -> str:
    if not RE_DATA_HORA.fullmatch(v):
        raise ValueError("data_hora invalida")
    datetime.strptime(v, "%Y-%m-%dT%H:%M:%S")
    return v


def _validar_nao_vazio(v: str) -> str:
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
        return _validar_nao_vazio(v)

    @field_validator("email")
    @classmethod
    def _v_email(cls, v):
        if not RE_EMAIL.match(v):
            raise ValueError("email invalido")
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
        return _validar_nao_vazio(v)

    @field_validator("data_hora")
    @classmethod
    def _v_data_hora(cls, v):
        return _validar_data_hora(v)


class AtualizacaoEntrada(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: Literal["COLETADO", "EM_TRANSITO", "SAIU_PARA_ENTREGA", "ENTREGUE", "FALHA_ENTREGA"]
    data_hora: StrictStr
    local: StrictStr

    @field_validator("data_hora")
    @classmethod
    def _v_data_hora(cls, v):
        return _validar_data_hora(v)

    @field_validator("local")
    @classmethod
    def _v_local(cls, v):
        return _validar_nao_vazio(v)


class CancelamentoEntrada(BaseModel):
    model_config = ConfigDict(extra="ignore")

    data_hora: StrictStr

    @field_validator("data_hora")
    @classmethod
    def _v_data_hora(cls, v):
        return _validar_data_hora(v)


def _indisponivel(nome: str) -> ErroAPI:
    return ErroAPI(503, "SERVICO_INDISPONIVEL", "Servico %s indisponivel." % nome)


def _buscar_cotacao(cotacao_id: str) -> dict:
    url = os.environ.get("URL_COTACAO", "") + "/cotacoes/" + quote(cotacao_id, safe="")
    try:
        r = httpx.get(url, timeout=10.0)
    except Exception:
        raise _indisponivel("de cotacao")
    if r.status_code >= 500:
        raise _indisponivel("de cotacao")
    if r.status_code >= 400:
        raise ErroAPI(422, "COTACAO_NAO_ENCONTRADA", "Cotacao nao encontrada.")
    try:
        dados = r.json()
    except Exception:
        raise _indisponivel("de cotacao")
    if not isinstance(dados, dict):
        raise _indisponivel("de cotacao")
    return dados


def _publicar(tipo: str, ocorrido_em: str, dados: dict) -> None:
    evento = {
        "id": str(uuid.uuid4()),
        "tipo": tipo,
        "ocorrido_em": ocorrido_em,
        "dados": dados,
    }
    url = os.environ.get("URL_BARRAMENTO", "") + "/publicar"
    try:
        r = httpx.post(url, json=evento, timeout=10.0)
    except Exception:
        raise _indisponivel("de barramento")
    if r.status_code >= 400:
        raise _indisponivel("de barramento")


def _evento_status(envio: dict, item: dict) -> dict:
    return {
        "envio_id": envio["id"],
        "codigo_rastreio": envio["codigo_rastreio"],
        "pedido_id": envio["pedido_id"],
        "status": item["status"],
        "data_hora": item["data_hora"],
        "local": item["local"],
        "destinatario_email": envio["destinatario"]["email"],
    }


def _obter_envio(envio_id: str) -> dict:
    envio = _estado["envios"].get(envio_id)
    if envio is None:
        raise ErroAPI(404, "ENVIO_NAO_ENCONTRADO", "Envio nao encontrado.")
    return envio


def _verificar_data_hora(envio: dict, data_hora: str) -> None:
    ultimo = envio["historico"][-1]["data_hora"]
    if datetime.strptime(data_hora, "%Y-%m-%dT%H:%M:%S") < datetime.strptime(ultimo, "%Y-%m-%dT%H:%M:%S"):
        raise ErroAPI(422, "DATA_HORA_INVALIDA", "Data e hora anteriores ao ultimo item do historico.")


@app.post("/admin/reset")
def reset():
    with _lock:
        _estado["contador"] = 0
        _estado["envios"].clear()
    return Response(status_code=204)


@app.post("/envios", status_code=201)
def criar_envio(entrada: EnvioEntrada):
    with _lock:
        for e in _estado["envios"].values():
            if e["pedido_id"] == entrada.pedido_id and e["status"] != "CANCELADO":
                raise ErroAPI(409, "ENVIO_DUPLICADO", "O pedido ja possui envio ativo.")

        cotacao = _buscar_cotacao(entrada.cotacao_id)

        try:
            valida_ate = date.fromisoformat(str(cotacao.get("valida_ate")))
        except Exception:
            raise _indisponivel("de cotacao")
        data_envio = datetime.strptime(entrada.data_hora, "%Y-%m-%dT%H:%M:%S").date()
        if data_envio > valida_ate:
            raise ErroAPI(422, "COTACAO_EXPIRADA", "A cotacao esta expirada.")

        opcao = None
        for o in cotacao.get("opcoes") or []:
            if isinstance(o, dict) and o.get("transportadora_id") == entrada.transportadora_id:
                opcao = o
                break
        if opcao is None:
            raise ErroAPI(422, "TRANSPORTADORA_NAO_OFERECIDA", "Transportadora nao oferecida na cotacao.")

        numero = _estado["contador"] + 1
        envio = {
            "id": "ENV-%06d" % numero,
            "codigo_rastreio": "LG%09dBR" % numero,
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

        _publicar(
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

        _estado["contador"] = numero
        _estado["envios"][envio["id"]] = envio
        return JSONResponse(status_code=201, content=copy.deepcopy(envio))


@app.get("/envios")
def listar_envios(pedido_id: str):
    with _lock:
        lista = [copy.deepcopy(e) for e in _estado["envios"].values() if e["pedido_id"] == pedido_id]
    lista.sort(key=lambda e: e["id"])
    return lista


@app.get("/envios/{envio_id}")
def obter_envio(envio_id: str):
    with _lock:
        return copy.deepcopy(_obter_envio(envio_id))


@app.post("/envios/{envio_id}/atualizacoes")
def atualizar_envio(envio_id: str, entrada: AtualizacaoEntrada):
    with _lock:
        atual = _obter_envio(envio_id)
        permitidos = TRANSICOES.get(atual["status"], set())
        if entrada.status not in permitidos:
            raise ErroAPI(409, "TRANSICAO_INVALIDA", "Transicao de status invalida.")
        _verificar_data_hora(atual, entrada.data_hora)

        novo = copy.deepcopy(atual)
        itens_novos = []
        item = {"status": entrada.status, "data_hora": entrada.data_hora, "local": entrada.local}
        novo["historico"].append(item)
        novo["status"] = entrada.status
        itens_novos.append(item)
        if entrada.status == "FALHA_ENTREGA":
            novo["tentativas_falhas"] += 1
            if novo["tentativas_falhas"] >= MAX_FALHAS:
                devolvido = {"status": "DEVOLVIDO", "data_hora": entrada.data_hora, "local": entrada.local}
                novo["historico"].append(devolvido)
                novo["status"] = "DEVOLVIDO"
                itens_novos.append(devolvido)

        for it in itens_novos:
            _publicar("envio.status_atualizado", entrada.data_hora, _evento_status(novo, it))

        _estado["envios"][envio_id] = novo
        return copy.deepcopy(novo)


@app.post("/envios/{envio_id}/cancelar")
def cancelar_envio(envio_id: str, entrada: CancelamentoEntrada):
    with _lock:
        atual = _obter_envio(envio_id)
        if atual["status"] != "CRIADO":
            raise ErroAPI(409, "TRANSICAO_INVALIDA", "Somente envios criados podem ser cancelados.")
        _verificar_data_hora(atual, entrada.data_hora)

        novo = copy.deepcopy(atual)
        item = {"status": "CANCELADO", "data_hora": entrada.data_hora, "local": None}
        novo["historico"].append(item)
        novo["status"] = "CANCELADO"

        _publicar("envio.status_atualizado", entrada.data_hora, _evento_status(novo, item))

        _estado["envios"][envio_id] = novo
        return copy.deepcopy(novo)
