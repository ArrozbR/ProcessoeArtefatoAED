# arquivo: servicos/rastreamento/main.py
import copy
import re
import threading
from datetime import date
from typing import Any, Dict, Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, StrictStr
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()


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


RE_DATA = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
STATUS_FINAIS = {"ENTREGUE", "CANCELADO", "DEVOLVIDO"}

_lock = threading.RLock()
_rastreamentos = {}
_processados = set()


class Evento(BaseModel):
    id: StrictStr
    tipo: StrictStr
    ocorrido_em: StrictStr
    dados: Dict[str, Any]


def _dados_invalidos():
    return ErroAPI(422, "DADOS_INVALIDOS", "Dados do evento inválidos.")


@app.post("/admin/reset", status_code=204)
def reset():
    with _lock:
        _rastreamentos.clear()
        _processados.clear()
    return Response(status_code=204)


@app.post("/eventos")
def receber_evento(evento: Evento):
    with _lock:
        if evento.id in _processados:
            return {"processado": False}
        d = evento.dados
        if evento.tipo == "envio.criado":
            campos = ["envio_id", "codigo_rastreio", "pedido_id", "transportadora_id",
                      "data_prevista_entrega", "data_hora"]
            if any(c not in d for c in campos):
                raise _dados_invalidos()
            _rastreamentos[d["codigo_rastreio"]] = {
                "codigo_rastreio": d["codigo_rastreio"],
                "envio_id": d["envio_id"],
                "pedido_id": d["pedido_id"],
                "transportadora_id": d["transportadora_id"],
                "data_prevista_entrega": d["data_prevista_entrega"],
                "historico": [{"status": "CRIADO", "data_hora": d["data_hora"], "local": None}],
            }
            _processados.add(evento.id)
            return {"processado": True}
        if evento.tipo == "envio.status_atualizado":
            campos = ["codigo_rastreio", "status", "data_hora"]
            if any(c not in d for c in campos):
                raise _dados_invalidos()
            r = _rastreamentos.get(d["codigo_rastreio"])
            if r is None:
                return {"processado": False}
            r["historico"].append({"status": d["status"], "data_hora": d["data_hora"], "local": d.get("local")})
            _processados.add(evento.id)
            return {"processado": True}
        return {"processado": False}


@app.get("/rastreamento/{codigo_rastreio}")
def obter_rastreamento(codigo_rastreio: str, data: Optional[str] = None):
    data_ref = None
    if data is not None:
        if not RE_DATA.match(data):
            raise _dados_invalidos()
        try:
            data_ref = date.fromisoformat(data)
        except ValueError:
            raise _dados_invalidos()
    with _lock:
        r = _rastreamentos.get(codigo_rastreio)
        if r is None:
            raise ErroAPI(404, "RASTREIO_NAO_ENCONTRADO", "Código de rastreio não encontrado.")
        r = copy.deepcopy(r)
    status = r["historico"][-1]["status"]
    atrasado = False
    if data_ref is not None and status not in STATUS_FINAIS:
        try:
            atrasado = data_ref > date.fromisoformat(r["data_prevista_entrega"])
        except (TypeError, ValueError):
            atrasado = False
    return {
        "codigo_rastreio": r["codigo_rastreio"],
        "envio_id": r["envio_id"],
        "pedido_id": r["pedido_id"],
        "transportadora_id": r["transportadora_id"],
        "data_prevista_entrega": r["data_prevista_entrega"],
        "status": status,
        "entregue": status == "ENTREGUE",
        "atrasado": atrasado,
        "historico": r["historico"],
    }
