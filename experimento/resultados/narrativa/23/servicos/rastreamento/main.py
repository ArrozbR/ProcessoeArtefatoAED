# arquivo: servicos/rastreamento/main.py
"""Microsserviço de Rastreamento."""
import copy
import json
import re
import threading
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

DATA_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
STATUS_FINAIS = {"ENTREGUE", "CANCELADO", "DEVOLVIDO"}

_lock = threading.Lock()
_rastreamentos: dict = {}
_eventos_processados: set = set()


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


def _invalido() -> ErroAPI:
    return ErroAPI(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.post("/admin/reset", status_code=204)
def reset():
    with _lock:
        _rastreamentos.clear()
        _eventos_processados.clear()
    return Response(status_code=204)


@app.post("/eventos")
async def receber_evento(request: Request):
    try:
        corpo = json.loads(await request.body())
    except Exception:
        raise _invalido()
    if not isinstance(corpo, dict):
        raise _invalido()
    for campo in ("id", "tipo", "ocorrido_em", "dados"):
        if campo not in corpo or corpo[campo] is None:
            raise _invalido()
    if not isinstance(corpo["dados"], dict):
        raise _invalido()

    evento_id = corpo["id"]
    tipo = corpo["tipo"]
    dados = corpo["dados"]
    try:
        chave = json.dumps(evento_id, sort_keys=True)
    except Exception:
        raise _invalido()

    with _lock:
        if chave in _eventos_processados:
            return {"processado": False}

        if tipo == "envio.criado":
            try:
                codigo = dados["codigo_rastreio"]
                registro = {
                    "codigo_rastreio": codigo,
                    "envio_id": dados["envio_id"],
                    "pedido_id": dados["pedido_id"],
                    "transportadora_id": dados["transportadora_id"],
                    "data_prevista_entrega": dados["data_prevista_entrega"],
                    "historico": [
                        {"status": "CRIADO", "data_hora": dados["data_hora"], "local": None}
                    ],
                }
            except (KeyError, TypeError):
                raise _invalido()
            if not isinstance(codigo, str):
                raise _invalido()
            _rastreamentos[codigo] = registro
            _eventos_processados.add(chave)
            return {"processado": True}

        if tipo == "envio.status_atualizado":
            try:
                codigo = dados["codigo_rastreio"]
                item = {
                    "status": dados["status"],
                    "data_hora": dados["data_hora"],
                    "local": dados.get("local"),
                }
            except (KeyError, TypeError):
                raise _invalido()
            if not isinstance(codigo, str) or codigo not in _rastreamentos:
                return {"processado": False}
            _rastreamentos[codigo]["historico"].append(item)
            _eventos_processados.add(chave)
            return {"processado": True}

        return {"processado": False}


@app.get("/rastreamento/{codigo_rastreio}")
def obter_rastreamento(codigo_rastreio: str, data: Optional[str] = Query(None)):
    if data is not None:
        if not DATA_RE.fullmatch(data):
            raise _invalido()
        try:
            datetime.strptime(data, "%Y-%m-%d")
        except ValueError:
            raise _invalido()

    with _lock:
        r = _rastreamentos.get(codigo_rastreio)
        if r is None:
            raise ErroAPI(404, "RASTREIO_NAO_ENCONTRADO", "Código de rastreio não encontrado.")
        r = copy.deepcopy(r)

    status = r["historico"][-1]["status"]
    entregue = status == "ENTREGUE"
    atrasado = False
    if data is not None and status not in STATUS_FINAIS:
        prevista = r["data_prevista_entrega"]
        if isinstance(prevista, str) and data > prevista:
            atrasado = True
    return {
        "codigo_rastreio": r["codigo_rastreio"],
        "envio_id": r["envio_id"],
        "pedido_id": r["pedido_id"],
        "transportadora_id": r["transportadora_id"],
        "data_prevista_entrega": r["data_prevista_entrega"],
        "status": status,
        "entregue": entregue,
        "atrasado": atrasado,
        "historico": r["historico"],
    }
