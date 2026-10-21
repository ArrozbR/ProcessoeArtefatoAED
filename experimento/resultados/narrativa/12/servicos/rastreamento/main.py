# arquivo: servicos/rastreamento/main.py
import copy
import re
import threading
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
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


class EventoEntrada(BaseModel):
    id: str = Field(..., strict=True)
    tipo: str = Field(..., strict=True)
    ocorrido_em: str = Field(..., strict=True)
    dados: Dict[str, Any]


_trava = threading.RLock()
_rastreamentos: dict = {}
_processados: set = set()

STATUS_FINAIS = {"ENTREGUE", "CANCELADO", "DEVOLVIDO"}


@app.post("/admin/reset", status_code=204)
def reset():
    with _trava:
        _rastreamentos.clear()
        _processados.clear()
    return Response(status_code=204)


@app.post("/eventos")
def receber_evento(evento: EventoEntrada):
    with _trava:
        if evento.id in _processados:
            return {"processado": False}
        dados = evento.dados
        if evento.tipo == "envio.criado":
            codigo = dados.get("codigo_rastreio")
            if not isinstance(codigo, str):
                raise ErroAPI(422, "DADOS_INVALIDOS", "Dados do evento inválidos.")
            _rastreamentos[codigo] = {
                "codigo_rastreio": codigo,
                "envio_id": dados.get("envio_id"),
                "pedido_id": dados.get("pedido_id"),
                "transportadora_id": dados.get("transportadora_id"),
                "data_prevista_entrega": dados.get("data_prevista_entrega"),
                "historico": [{"status": "CRIADO", "data_hora": dados.get("data_hora"), "local": None}],
            }
            _processados.add(evento.id)
            return {"processado": True}
        if evento.tipo == "envio.status_atualizado":
            codigo = dados.get("codigo_rastreio")
            r = _rastreamentos.get(codigo) if isinstance(codigo, str) else None
            if r is None:
                return {"processado": False}
            r["historico"].append(
                {"status": dados.get("status"), "data_hora": dados.get("data_hora"), "local": dados.get("local")}
            )
            _processados.add(evento.id)
            return {"processado": True}
        return {"processado": False}


@app.get("/rastreamento/{codigo_rastreio}")
def obter_rastreamento(codigo_rastreio: str, data: Optional[str] = Query(None)):
    if data is not None:
        valido = bool(re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", data))
        if valido:
            try:
                datetime.strptime(data, "%Y-%m-%d")
            except ValueError:
                valido = False
        if not valido:
            raise ErroAPI(422, "DADOS_INVALIDOS", "Data inválida.")
    with _trava:
        r = _rastreamentos.get(codigo_rastreio)
        if r is None:
            raise ErroAPI(404, "RASTREIO_NAO_ENCONTRADO", "Rastreio não encontrado.")
        status = r["historico"][-1]["status"]
        prevista = r["data_prevista_entrega"]
        atrasado = bool(
            data is not None
            and status not in STATUS_FINAIS
            and isinstance(prevista, str)
            and data > prevista
        )
        return {
            "codigo_rastreio": r["codigo_rastreio"],
            "envio_id": r["envio_id"],
            "pedido_id": r["pedido_id"],
            "transportadora_id": r["transportadora_id"],
            "data_prevista_entrega": prevista,
            "status": status,
            "entregue": status == "ENTREGUE",
            "atrasado": atrasado,
            "historico": copy.deepcopy(r["historico"]),
        }
