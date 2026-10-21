# arquivo: servicos/rastreamento/main.py
import copy
import re
import threading
from datetime import date
from typing import Any, Dict, Optional

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, StrictStr
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

_lock = threading.RLock()
_rastreamentos = {}
_eventos_processados = set()


class ErroApi(Exception):
    def __init__(self, status, codigo, mensagem):
        self.status = status
        self.codigo = codigo
        self.mensagem = mensagem


def _erro(status, codigo, mensagem):
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


@app.exception_handler(ErroApi)
async def _tratar_erro_api(request: Request, exc: ErroApi):
    return _erro(exc.status, exc.codigo, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request: Request, exc: RequestValidationError):
    return _erro(422, "DADOS_INVALIDOS", "Dados inválidos.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return _erro(404, "NAO_ENCONTRADO", "Recurso não encontrado.")
    if exc.status_code == 405:
        return _erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return _erro(exc.status_code, "ERRO", str(exc.detail))


class Evento(BaseModel):
    id: StrictStr
    tipo: StrictStr
    ocorrido_em: StrictStr
    dados: Dict[str, Any]


CAMPOS_CRIADO = [
    "envio_id",
    "codigo_rastreio",
    "pedido_id",
    "transportadora_id",
    "data_prevista_entrega",
    "data_hora",
]
CAMPOS_STATUS = ["codigo_rastreio", "status", "data_hora"]


def _dados_invalidos():
    return ErroApi(422, "DADOS_INVALIDOS", "Dados inválidos.")


@app.post("/admin/reset")
def reset():
    with _lock:
        _rastreamentos.clear()
        _eventos_processados.clear()
    return Response(status_code=204)


@app.post("/eventos")
def receber_evento(evento: Evento):
    dados = evento.dados
    with _lock:
        if evento.tipo == "envio.criado":
            for campo in CAMPOS_CRIADO:
                if campo not in dados:
                    raise _dados_invalidos()
        elif evento.tipo == "envio.status_atualizado":
            for campo in CAMPOS_STATUS:
                if campo not in dados:
                    raise _dados_invalidos()
        else:
            return {"processado": False}

        if evento.id in _eventos_processados:
            return {"processado": False}

        if evento.tipo == "envio.criado":
            codigo = dados["codigo_rastreio"]
            _rastreamentos[codigo] = {
                "codigo_rastreio": codigo,
                "envio_id": dados["envio_id"],
                "pedido_id": dados["pedido_id"],
                "transportadora_id": dados["transportadora_id"],
                "data_prevista_entrega": dados["data_prevista_entrega"],
                "historico": [{"status": "CRIADO", "data_hora": dados["data_hora"], "local": None}],
            }
        else:
            codigo = dados["codigo_rastreio"]
            rastreamento = _rastreamentos.get(codigo)
            if rastreamento is None:
                return {"processado": False}
            rastreamento["historico"].append(
                {
                    "status": dados["status"],
                    "data_hora": dados["data_hora"],
                    "local": dados.get("local"),
                }
            )
        _eventos_processados.add(evento.id)
        return {"processado": True}


@app.get("/rastreamento/{codigo_rastreio}")
def obter_rastreamento(codigo_rastreio: str, data: Optional[str] = Query(None)):
    data_consulta = None
    if data is not None:
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", data):
            raise _dados_invalidos()
        try:
            data_consulta = date.fromisoformat(data)
        except ValueError:
            raise _dados_invalidos()

    with _lock:
        rastreamento = _rastreamentos.get(codigo_rastreio)
        if rastreamento is None:
            raise ErroApi(404, "RASTREIO_NAO_ENCONTRADO", "Rastreio não encontrado.")
        rastreamento = copy.deepcopy(rastreamento)

    status = rastreamento["historico"][-1]["status"]
    atrasado = False
    if data_consulta is not None and status not in ("ENTREGUE", "CANCELADO", "DEVOLVIDO"):
        try:
            prevista = date.fromisoformat(rastreamento["data_prevista_entrega"])
            atrasado = data_consulta > prevista
        except (TypeError, ValueError):
            atrasado = False

    return {
        "codigo_rastreio": rastreamento["codigo_rastreio"],
        "envio_id": rastreamento["envio_id"],
        "pedido_id": rastreamento["pedido_id"],
        "transportadora_id": rastreamento["transportadora_id"],
        "data_prevista_entrega": rastreamento["data_prevista_entrega"],
        "status": status,
        "entregue": status == "ENTREGUE",
        "atrasado": atrasado,
        "historico": rastreamento["historico"],
    }
