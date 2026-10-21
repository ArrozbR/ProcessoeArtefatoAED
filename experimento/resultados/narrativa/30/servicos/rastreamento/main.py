# arquivo: servicos/rastreamento/main.py
import copy
import re
import threading
from datetime import date
from typing import Any, Dict, Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

_lock = threading.Lock()
_rastreamentos = {}
_eventos_processados = set()


class ErroAPI(Exception):
    def __init__(self, status: int, codigo: str, mensagem: str):
        self.status = status
        self.codigo = codigo
        self.mensagem = mensagem


def _erro(status, codigo, mensagem):
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request: Request, exc: ErroAPI):
    return _erro(exc.status, exc.codigo, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request: Request, exc: RequestValidationError):
    return _erro(422, "DADOS_INVALIDOS", "Dados invalidos na requisicao.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request: Request, exc: StarletteHTTPException):
    codigos = {404: "NAO_ENCONTRADO", 405: "METODO_NAO_PERMITIDO"}
    return _erro(exc.status_code, codigos.get(exc.status_code, "ERRO"), str(exc.detail))


class Evento(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(strict=True)
    tipo: str = Field(strict=True)
    ocorrido_em: str = Field(strict=True)
    dados: Dict[str, Any]


@app.post("/admin/reset", status_code=204)
def reset():
    with _lock:
        _rastreamentos.clear()
        _eventos_processados.clear()
    return Response(status_code=204)


@app.post("/eventos")
def receber_evento(evento: Evento):
    with _lock:
        if evento.id in _eventos_processados:
            return {"processado": False}
        d = evento.dados
        if evento.tipo == "envio.criado":
            codigo = d.get("codigo_rastreio")
            _rastreamentos[codigo] = {
                "codigo_rastreio": codigo,
                "envio_id": d.get("envio_id"),
                "pedido_id": d.get("pedido_id"),
                "transportadora_id": d.get("transportadora_id"),
                "data_prevista_entrega": d.get("data_prevista_entrega"),
                "historico": [
                    {"status": "CRIADO", "data_hora": d.get("data_hora", evento.ocorrido_em), "local": None}
                ],
            }
            _eventos_processados.add(evento.id)
            return {"processado": True}
        if evento.tipo == "envio.status_atualizado":
            codigo = d.get("codigo_rastreio")
            r = _rastreamentos.get(codigo)
            if r is None:
                return {"processado": False}
            r["historico"].append(
                {
                    "status": d.get("status"),
                    "data_hora": d.get("data_hora", evento.ocorrido_em),
                    "local": d.get("local"),
                }
            )
            _eventos_processados.add(evento.id)
            return {"processado": True}
        return {"processado": False}


@app.get("/rastreamento/{codigo_rastreio}")
def obter_rastreamento(codigo_rastreio: str, data: Optional[str] = None):
    data_ref = None
    if data is not None:
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", data):
            raise ErroAPI(422, "DADOS_INVALIDOS", "Data invalida.")
        try:
            data_ref = date.fromisoformat(data)
        except ValueError:
            raise ErroAPI(422, "DADOS_INVALIDOS", "Data invalida.")
    with _lock:
        r = _rastreamentos.get(codigo_rastreio)
        if r is None:
            raise ErroAPI(404, "RASTREIO_NAO_ENCONTRADO", "Codigo de rastreio nao encontrado.")
        r = copy.deepcopy(r)
    status = r["historico"][-1]["status"]
    atrasado = False
    if data_ref is not None and status not in ("ENTREGUE", "CANCELADO", "DEVOLVIDO"):
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
