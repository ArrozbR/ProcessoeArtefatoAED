# arquivo: servicos/rastreamento/main.py
import copy
import json
import re
import threading
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
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


RE_DATA = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
STATUS_FINAIS = {"ENTREGUE", "CANCELADO", "DEVOLVIDO"}

_lock = threading.RLock()
_rastreamentos = {}
_eventos_processados = set()


class Evento(BaseModel):
    id: Any
    tipo: Any
    ocorrido_em: Any
    dados: Dict[str, Any]


def _chave(valor: Any) -> str:
    if isinstance(valor, str):
        return valor
    return json.dumps(valor, sort_keys=True)


@app.post("/admin/reset", status_code=204)
def reset():
    with _lock:
        _rastreamentos.clear()
        _eventos_processados.clear()
    return Response(status_code=204)


@app.post("/eventos")
def receber_evento(evento: Evento):
    with _lock:
        chave = _chave(evento.id)
        if chave in _eventos_processados:
            return {"processado": False}
        dados = evento.dados
        if evento.tipo == "envio.criado":
            codigo = dados.get("codigo_rastreio")
            data_hora = dados.get("data_hora", evento.ocorrido_em)
            _rastreamentos[codigo] = {
                "codigo_rastreio": codigo,
                "envio_id": dados.get("envio_id"),
                "pedido_id": dados.get("pedido_id"),
                "transportadora_id": dados.get("transportadora_id"),
                "data_prevista_entrega": dados.get("data_prevista_entrega"),
                "historico": [{"status": "CRIADO", "data_hora": data_hora, "local": None}],
            }
            _eventos_processados.add(chave)
            return {"processado": True}
        if evento.tipo == "envio.status_atualizado":
            codigo = dados.get("codigo_rastreio")
            rastreamento = _rastreamentos.get(codigo) if isinstance(codigo, str) else None
            if rastreamento is None:
                return {"processado": False}
            rastreamento["historico"].append(
                {
                    "status": dados.get("status"),
                    "data_hora": dados.get("data_hora", evento.ocorrido_em),
                    "local": dados.get("local"),
                }
            )
            _eventos_processados.add(chave)
            return {"processado": True}
        return {"processado": False}


@app.get("/rastreamento/{codigo_rastreio}")
def obter_rastreamento(codigo_rastreio: str, data: Optional[str] = None):
    data_ref = None
    if data is not None:
        if not RE_DATA.fullmatch(data):
            raise ErroAPI(422, "DADOS_INVALIDOS", "Data mal formada.")
        try:
            data_ref = datetime.strptime(data, "%Y-%m-%d").date()
        except ValueError:
            raise ErroAPI(422, "DADOS_INVALIDOS", "Data mal formada.")
    with _lock:
        r = _rastreamentos.get(codigo_rastreio)
        if r is None:
            raise ErroAPI(404, "RASTREIO_NAO_ENCONTRADO", "Código de rastreio não encontrado.")
        status = r["historico"][-1]["status"]
        atrasado = False
        if data_ref is not None and status not in STATUS_FINAIS:
            try:
                prevista = datetime.strptime(r["data_prevista_entrega"], "%Y-%m-%d").date()
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
            "historico": copy.deepcopy(r["historico"]),
        }
