# arquivo: servicos/envios/main.py
import copy
import os
import re
import threading
import uuid
from datetime import datetime
from typing import Literal
from urllib.parse import quote

import httpx
from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field, field_validator
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
    return JSONResponse(status_code=422, content={"erro": "DADOS_INVALIDOS", "mensagem": "Dados inválidos."})


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request: Request, exc: StarletteHTTPException):
    codigos = {404: "NAO_ENCONTRADO", 405: "METODO_NAO_PERMITIDO"}
    return JSONResponse(
        status_code=exc.status_code,
        content={"erro": codigos.get(exc.status_code, "ERRO_HTTP"), "mensagem": str(exc.detail)},
    )


_cliente = httpx.Client(timeout=10.0)


def _url(nome: str) -> str:
    return os.environ.get(nome, "")


def _validar_data_hora(v: str) -> str:
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}", v):
        raise ValueError("data e hora inválida")
    datetime.strptime(v, "%Y-%m-%dT%H:%M:%S")
    return v


class Destinatario(BaseModel):
    nome: str = Field(..., strict=True, min_length=1)
    email: str = Field(..., strict=True)

    @field_validator("email")
    @classmethod
    def _validar_email(cls, v: str) -> str:
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", v):
            raise ValueError("e-mail inválido")
        return v


class EnvioEntrada(BaseModel):
    pedido_id: str = Field(..., strict=True, min_length=1)
    cotacao_id: str = Field(..., strict=True, min_length=1)
    transportadora_id: str = Field(..., strict=True, min_length=1)
    destinatario: Destinatario
    data_hora: str = Field(..., strict=True)

    @field_validator("data_hora")
    @classmethod
    def _validar_dh(cls, v: str) -> str:
        return _validar_data_hora(v)


class AtualizacaoEntrada(BaseModel):
    status: Literal["COLETADO", "EM_TRANSITO", "SAIU_PARA_ENTREGA", "ENTREGUE", "FALHA_ENTREGA"]
    data_hora: str = Field(..., strict=True)
    local: str = Field(..., strict=True, min_length=1)

    @field_validator("data_hora")
    @classmethod
    def _validar_dh(cls, v: str) -> str:
        return _validar_data_hora(v)


class CancelamentoEntrada(BaseModel):
    data_hora: str = Field(..., strict=True)

    @field_validator("data_hora")
    @classmethod
    def _validar_dh(cls, v: str) -> str:
        return _validar_data_hora(v)


TRANSICOES = {
    "CRIADO": {"COLETADO"},
    "COLETADO": {"EM_TRANSITO"},
    "EM_TRANSITO": {"EM_TRANSITO", "SAIU_PARA_ENTREGA"},
    "SAIU_PARA_ENTREGA": {"ENTREGUE", "FALHA_ENTREGA"},
    "FALHA_ENTREGA": {"SAIU_PARA_ENTREGA"},
}

_trava = threading.RLock()
_envios: dict = {}
_contador = {"envio": 0}


def _indisponivel(servico: str) -> ErroAPI:
    return ErroAPI(503, "SERVICO_INDISPONIVEL", f"Serviço de {servico} indisponível.")


def _publicar(tipo: str, ocorrido_em: str, dados: dict) -> None:
    evento = {"id": str(uuid.uuid4()), "tipo": tipo, "ocorrido_em": ocorrido_em, "dados": dados}
    try:
        r = _cliente.post(f"{_url('URL_BARRAMENTO')}/publicar", json=evento)
    except httpx.HTTPError:
        raise _indisponivel("barramento")
    if r.status_code >= 300:
        raise _indisponivel("barramento")


def _buscar_cotacao(cotacao_id: str):
    try:
        r = _cliente.get(f"{_url('URL_COTACAO')}/cotacoes/{quote(cotacao_id, safe='')}")
    except httpx.HTTPError:
        raise _indisponivel("cotação")
    if r.status_code >= 500:
        raise _indisponivel("cotação")
    if 400 <= r.status_code < 500:
        return None
    if r.status_code != 200:
        raise _indisponivel("cotação")
    try:
        dados = r.json()
    except ValueError:
        raise _indisponivel("cotação")
    if not isinstance(dados, dict):
        raise _indisponivel("cotação")
    return dados


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


@app.post("/admin/reset", status_code=204)
def reset():
    with _trava:
        _envios.clear()
        _contador["envio"] = 0
    return Response(status_code=204)


@app.post("/envios", status_code=201)
def criar_envio(entrada: EnvioEntrada):
    with _trava:
        for e in _envios.values():
            if e["pedido_id"] == entrada.pedido_id and e["status"] != "CANCELADO":
                raise ErroAPI(409, "ENVIO_DUPLICADO", "O pedido já possui um envio ativo.")

        cotacao = _buscar_cotacao(entrada.cotacao_id)
        if cotacao is None:
            raise ErroAPI(422, "COTACAO_NAO_ENCONTRADA", "Cotação não encontrada.")

        if entrada.data_hora[:10] > str(cotacao.get("valida_ate", "")):
            raise ErroAPI(422, "COTACAO_EXPIRADA", "A cotação está expirada.")

        opcao = None
        for o in cotacao.get("opcoes") or []:
            if isinstance(o, dict) and o.get("transportadora_id") == entrada.transportadora_id:
                opcao = o
                break
        if opcao is None:
            raise ErroAPI(422, "TRANSPORTADORA_NAO_OFERECIDA", "A transportadora não foi oferecida na cotação.")

        numero = _contador["envio"] + 1
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

        _contador["envio"] = numero
        _envios[envio["id"]] = envio
        return JSONResponse(status_code=201, content=copy.deepcopy(envio))


@app.get("/envios")
def listar_envios(pedido_id: str = Query(...)):
    with _trava:
        itens = [copy.deepcopy(e) for _, e in sorted(_envios.items()) if e["pedido_id"] == pedido_id]
    return itens


@app.get("/envios/{envio_id}")
def obter_envio(envio_id: str):
    with _trava:
        e = _envios.get(envio_id)
        if e is None:
            raise ErroAPI(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
        return copy.deepcopy(e)


@app.post("/envios/{envio_id}/atualizacoes")
def atualizar_envio(envio_id: str, entrada: AtualizacaoEntrada):
    with _trava:
        atual = _envios.get(envio_id)
        if atual is None:
            raise ErroAPI(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
        if entrada.status not in TRANSICOES.get(atual["status"], set()):
            raise ErroAPI(409, "TRANSICAO_INVALIDA", "Transição de status inválida.")
        if entrada.data_hora < atual["historico"][-1]["data_hora"]:
            raise ErroAPI(422, "DATA_HORA_INVALIDA", "Data e hora anterior ao último registro.")

        novo = copy.deepcopy(atual)
        novos_itens = []
        item = {"status": entrada.status, "data_hora": entrada.data_hora, "local": entrada.local}
        novo["historico"].append(item)
        novo["status"] = entrada.status
        novos_itens.append(item)
        if entrada.status == "FALHA_ENTREGA":
            novo["tentativas_falhas"] += 1
            if novo["tentativas_falhas"] >= 3:
                devolvido = {"status": "DEVOLVIDO", "data_hora": entrada.data_hora, "local": entrada.local}
                novo["historico"].append(devolvido)
                novo["status"] = "DEVOLVIDO"
                novos_itens.append(devolvido)

        for it in novos_itens:
            _publicar("envio.status_atualizado", entrada.data_hora, _evento_status(novo, it))

        _envios[envio_id] = novo
        return copy.deepcopy(novo)


@app.post("/envios/{envio_id}/cancelar")
def cancelar_envio(envio_id: str, entrada: CancelamentoEntrada):
    with _trava:
        atual = _envios.get(envio_id)
        if atual is None:
            raise ErroAPI(404, "ENVIO_NAO_ENCONTRADO", "Envio não encontrado.")
        if atual["status"] != "CRIADO":
            raise ErroAPI(409, "TRANSICAO_INVALIDA", "Só é possível cancelar envios criados.")
        if entrada.data_hora < atual["historico"][-1]["data_hora"]:
            raise ErroAPI(422, "DATA_HORA_INVALIDA", "Data e hora anterior ao último registro.")

        novo = copy.deepcopy(atual)
        item = {"status": "CANCELADO", "data_hora": entrada.data_hora, "local": None}
        novo["historico"].append(item)
        novo["status"] = "CANCELADO"

        _publicar("envio.status_atualizado", entrada.data_hora, _evento_status(novo, item))

        _envios[envio_id] = novo
        return copy.deepcopy(novo)
