# arquivo: servicos/rastreamento/main.py
import copy
import re
import threading
from datetime import date, datetime
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, StrictStr
from starlette.exceptions import HTTPException as StarletteHTTPException


class ErroApi(Exception):
    def __init__(self, status: int, codigo: str, mensagem: str):
        super().__init__(mensagem)
        self.status = status
        self.codigo = codigo
        self.mensagem = mensagem


app = FastAPI()


@app.exception_handler(ErroApi)
async def _tratar_erro_api(request: Request, exc: ErroApi):
    return JSONResponse(status_code=exc.status, content={"erro": exc.codigo, "mensagem": exc.mensagem})


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={"erro": "DADOS_INVALIDOS", "mensagem": "Dados inválidos."})


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request: Request, exc: StarletteHTTPException):
    codigos = {404: "NAO_ENCONTRADO", 405: "METODO_NAO_PERMITIDO"}
    return JSONResponse(
        status_code=exc.status_code,
        content={"erro": codigos.get(exc.status_code, "ERRO"), "mensagem": str(exc.detail)},
    )


RE_DATA = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
FINAIS = {"ENTREGUE", "CANCELADO", "DEVOLVIDO"}

_lock = threading.Lock()
_rastreamentos: dict = {}
_processados: set = set()


class Evento(BaseModel):
    id: StrictStr
    tipo: StrictStr
    ocorrido_em: StrictStr
    dados: dict


def _dados_invalidos():
    return ErroApi(422, "DADOS_INVALIDOS", "Dados inválidos.")


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
            try:
                codigo = d["codigo_rastreio"]
                registro = {
                    "codigo_rastreio": codigo,
                    "envio_id": d["envio_id"],
                    "pedido_id": d["pedido_id"],
                    "transportadora_id": d["transportadora_id"],
                    "data_prevista_entrega": d["data_prevista_entrega"],
                    "historico": [{"status": "CRIADO", "data_hora": d["data_hora"], "local": None}],
                }
            except KeyError:
                raise _dados_invalidos()
            _rastreamentos[codigo] = registro
            _processados.add(evento.id)
            return {"processado": True}
        if evento.tipo == "envio.status_atualizado":
            try:
                codigo = d["codigo_rastreio"]
                item = {"status": d["status"], "data_hora": d["data_hora"], "local": d.get("local")}
            except KeyError:
                raise _dados_invalidos()
            registro = _rastreamentos.get(codigo)
            if registro is None:
                return {"processado": False}
            registro["historico"].append(item)
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
            data_ref = datetime.strptime(data, "%Y-%m-%d").date()
        except ValueError:
            raise _dados_invalidos()
    with _lock:
        r = _rastreamentos.get(codigo_rastreio)
        if r is None:
            raise ErroApi(404, "RASTREIO_NAO_ENCONTRADO", "Código de rastreio não encontrado.")
        r = copy.deepcopy(r)
    status = r["historico"][-1]["status"]
    atrasado = False
    if data_ref is not None and status not in FINAIS:
        try:
            prevista = date.fromisoformat(r["data_prevista_entrega"])
            atrasado = data_ref > prevista
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
