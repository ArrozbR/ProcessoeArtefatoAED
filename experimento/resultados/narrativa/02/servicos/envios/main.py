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
from fastapi import FastAPI, Query
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


def _resposta_erro(status: int, codigo: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


app = FastAPI()


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request, exc: ErroAPI):
    return _resposta_erro(exc.status, exc.codigo, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request, exc: RequestValidationError):
    return _resposta_erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request, exc: StarletteHTTPException):
    codigos = {404: "NAO_ENCONTRADO", 405: "METODO_NAO_PERMITIDO"}
    return _resposta_erro(exc.status_code, codigos.get(exc.status_code, "ERRO"), str(exc.detail))


TIMEOUT = 10.0
FORMATO_DATA_HORA = "%Y-%m-%dT%H:%M:%S"
REGEX_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

TRANSICOES = {
    "CRIADO": {"COLETADO"},
    "COLETADO": {"EM_TRANSITO"},
    "EM_TRANSITO": {"EM_TRANSITO", "SAIU_PARA_ENTREGA"},
    "SAIU_PARA_ENTREGA": {"ENTREGUE", "FALHA_ENTREGA"},
    "FALHA_ENTREGA": {"SAIU_PARA_ENTREGA"},
}

_trava = threading.RLock()
_estado = {"contador": 0, "envios": {}}


def _validar_data_hora_texto(v: str) -> str:
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}", v):
        raise ValueError("data e hora mal formadas")
    datetime.strptime(v, FORMATO_DATA_HORA)
    return v


def _texto_nao_vazio(v: str) -> str:
    if not v.strip():
        raise ValueError("texto vazio")
    return v


class Destinatario(BaseModel):
    nome: StrictStr
    email: StrictStr

    @field_validator("nome")
    @classmethod
    def _validar_nome(cls, v: str) -> str:
        return _texto_nao_vazio(v)

    @field_validator("email")
    @classmethod
    def _validar_email(cls, v: str) -> str:
        if not REGEX_EMAIL.match(v):
            raise ValueError("e-mail inválido")
        return v


class EnvioEntrada(BaseModel):
    pedido_id: StrictStr
    cotacao_id: StrictStr
    transportadora_id: StrictStr
    destinatario: Destinatario
    data_hora: StrictStr

    @field_validator("pedido_id", "cotacao_id", "transportadora_id")
    @classmethod
    def _validar_textos(cls, v: str) -> str:
        return _texto_nao_vazio(v)

    @field_validator("data_hora")
    @classmethod
    def _validar_data_hora(cls, v: str) -> str:
        return _validar_data_hora_texto(v)


class AtualizacaoEntrada(BaseModel):
    status: Literal["COLETADO", "EM_TRANSITO", "SAIU_PARA_ENTREGA", "ENTREGUE", "FALHA_ENTREGA"]
    data_hora: StrictStr
    local: StrictStr

    @field_validator("data_hora")
    @classmethod
    def _validar_data_hora(cls, v: str) -> str:
        return _validar_data_hora_texto(v)

    @field_validator("local")
    @classmethod
    def _validar_local(cls, v: str) -> str:
        return _texto_nao_vazio(v)


class CancelamentoEntrada(BaseModel):
    data_hora: StrictStr

    @field_validator("data_hora")
    @classmethod
    def _validar_data_hora(cls, v: str) -> str:
        return _validar_data_hora_texto(v)


def _publicar(evento: dict) -> None:
    base = os.environ.get("URL_BARRAMENTO", "")
    try:
        resp = httpx.post(f"{base}/publicar", json=evento, timeout=TIMEOUT)
    except Exception:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Barramento de eventos indisponível.")
    if resp.status_code < 200 or resp.status_code >= 300:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Barramento de eventos indisponível.")


def _buscar_cotacao(cotacao_id: str) -> dict:
    base = os.environ.get("URL_COTACAO", "")
    try:
        resp = httpx.get(f"{base}/cotacoes/{quote(cotacao_id, safe='')}", timeout=TIMEOUT)
    except Exception:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Serviço de cotação indisponível.")
    if resp.status_code >= 500:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Serviço de cotação indisponível.")
    if resp.status_code != 200:
        raise ErroAPI(422, "COTACAO_NAO_ENCONTRADA", "Cotação não encontrada.")
    try:
        dados = resp.json()
    except Exception:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Resposta inválida do serviço de cotação.")
    if not isinstance(dados, dict):
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Resposta inválida do serviço de cotação.")
    return dados


def _evento(tipo: str, ocorrido_em: str, dados: dict) -> dict:
    return {"id": str(uuid.uuid4()), "tipo": tipo, "ocorrido_em": ocorrido_em, "dados": dados}


def _evento_status(envio: dict, item: dict) -> dict:
    return _evento("envio.status_atualizado", item["data_hora"], {
        "envio_id": envio["id"],
        "codigo_rastreio": envio["codigo_rastreio"],
        "pedido_id": envio["pedido_id"],
        "status": item["status"],
        "data_hora": item["data_hora"],
        "local": item["local"],
        "destinatario_email": envio["destinatario"]["email"],
    })


def _obter_envio(id: str) -> dict:
    envio = _estado["envios"].get(id)
    if envio is None:
        raise ErroAPI(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
    return envio


def _verificar_data_hora(envio: dict, data_hora: str) -> None:
    ultima = datetime.strptime(envio["historico"][-1]["data_hora"], FORMATO_DATA_HORA)
    nova = datetime.strptime(data_hora, FORMATO_DATA_HORA)
    if nova < ultima:
        raise ErroAPI(422, "DATA_HORA_INVALIDA", "Data e hora anteriores ao último item do histórico.")


@app.post("/admin/reset")
def reset():
    with _trava:
        _estado["contador"] = 0
        _estado["envios"] = {}
    return Response(status_code=204)


@app.post("/envios")
def criar_envio(dados: EnvioEntrada):
    with _trava:
        for e in _estado["envios"].values():
            if e["pedido_id"] == dados.pedido_id and e["status"] != "CANCELADO":
                raise ErroAPI(409, "ENVIO_DUPLICADO", "O pedido já possui um envio ativo.")

        cotacao = _buscar_cotacao(dados.cotacao_id)

        try:
            valida_ate = date.fromisoformat(str(cotacao.get("valida_ate")))
        except Exception:
            raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Resposta inválida do serviço de cotação.")
        data_envio = datetime.strptime(dados.data_hora, FORMATO_DATA_HORA).date()
        if data_envio > valida_ate:
            raise ErroAPI(422, "COTACAO_EXPIRADA", "A cotação está expirada.")

        opcao = None
        for op in cotacao.get("opcoes") or []:
            if isinstance(op, dict) and op.get("transportadora_id") == dados.transportadora_id:
                opcao = op
                break
        if opcao is None:
            raise ErroAPI(422, "TRANSPORTADORA_NAO_OFERECIDA", "Transportadora não oferecida na cotação.")

        numero = _estado["contador"] + 1
        envio = {
            "id": f"ENV-{numero:06d}",
            "codigo_rastreio": f"LG{numero:09d}BR",
            "pedido_id": dados.pedido_id,
            "cotacao_id": dados.cotacao_id,
            "transportadora_id": dados.transportadora_id,
            "preco_centavos": opcao.get("preco_centavos"),
            "data_prevista_entrega": opcao.get("data_prevista_entrega"),
            "destinatario": {"nome": dados.destinatario.nome, "email": dados.destinatario.email},
            "status": "CRIADO",
            "tentativas_falhas": 0,
            "historico": [{"status": "CRIADO", "data_hora": dados.data_hora, "local": None}],
        }
        evento = _evento("envio.criado", dados.data_hora, {
            "envio_id": envio["id"],
            "codigo_rastreio": envio["codigo_rastreio"],
            "pedido_id": envio["pedido_id"],
            "transportadora_id": envio["transportadora_id"],
            "data_prevista_entrega": envio["data_prevista_entrega"],
            "destinatario_email": envio["destinatario"]["email"],
            "data_hora": dados.data_hora,
        })
        _publicar(evento)

        _estado["contador"] = numero
        _estado["envios"][envio["id"]] = envio
        return JSONResponse(status_code=201, content=copy.deepcopy(envio))


@app.get("/envios")
def listar_envios(pedido_id: str = Query(...)):
    with _trava:
        lista = [copy.deepcopy(e) for e in _estado["envios"].values() if e["pedido_id"] == pedido_id]
    lista.sort(key=lambda e: e["id"])
    return JSONResponse(status_code=200, content=lista)


@app.get("/envios/{id}")
def obter_envio(id: str):
    with _trava:
        envio = _obter_envio(id)
        return JSONResponse(status_code=200, content=copy.deepcopy(envio))


@app.post("/envios/{id}/atualizacoes")
def atualizar_envio(id: str, dados: AtualizacaoEntrada):
    with _trava:
        atual = _obter_envio(id)
        if dados.status not in TRANSICOES.get(atual["status"], set()):
            raise ErroAPI(409, "TRANSICAO_INVALIDA", "Transição de status inválida.")
        _verificar_data_hora(atual, dados.data_hora)

        novo = copy.deepcopy(atual)
        itens = [{"status": dados.status, "data_hora": dados.data_hora, "local": dados.local}]
        novo["status"] = dados.status
        if dados.status == "FALHA_ENTREGA":
            novo["tentativas_falhas"] += 1
            if novo["tentativas_falhas"] >= 3:
                itens.append({"status": "DEVOLVIDO", "data_hora": dados.data_hora, "local": dados.local})
                novo["status"] = "DEVOLVIDO"
        novo["historico"].extend(itens)

        for item in itens:
            _publicar(_evento_status(novo, item))

        _estado["envios"][id] = novo
        return JSONResponse(status_code=200, content=copy.deepcopy(novo))


@app.post("/envios/{id}/cancelar")
def cancelar_envio(id: str, dados: CancelamentoEntrada):
    with _trava:
        atual = _obter_envio(id)
        if atual["status"] != "CRIADO":
            raise ErroAPI(409, "TRANSICAO_INVALIDA", "Só é possível cancelar envios no status CRIADO.")
        _verificar_data_hora(atual, dados.data_hora)

        novo = copy.deepcopy(atual)
        item = {"status": "CANCELADO", "data_hora": dados.data_hora, "local": None}
        novo["status"] = "CANCELADO"
        novo["historico"].append(item)

        _publicar(_evento_status(novo, item))

        _estado["envios"][id] = novo
        return JSONResponse(status_code=200, content=copy.deepcopy(novo))
