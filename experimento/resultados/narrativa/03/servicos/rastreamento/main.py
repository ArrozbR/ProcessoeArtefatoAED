# arquivo: servicos/rastreamento/main.py
import copy
import re
import threading
from datetime import date
from typing import Annotated, Any, Dict, Optional

from fastapi import FastAPI, Query
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
async def _tratar_erro_api(request, exc: ErroAPI):
    return JSONResponse(status_code=exc.status, content={"erro": exc.codigo, "mensagem": exc.mensagem})


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request, exc):
    return JSONResponse(status_code=422, content={"erro": "DADOS_INVALIDOS", "mensagem": "Dados invalidos."})


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request, exc: StarletteHTTPException):
    codigo = "NAO_ENCONTRADO" if exc.status_code == 404 else "ERRO_HTTP"
    return JSONResponse(status_code=exc.status_code, content={"erro": codigo, "mensagem": str(exc.detail)})


_RE_DATA = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")


def _data_valida(v: str) -> bool:
    if not _RE_DATA.match(v):
        return False
    try:
        date.fromisoformat(v)
    except ValueError:
        return False
    return True


class Evento(BaseModel):
    id: Annotated[str, Field(strict=True)]
    tipo: Annotated[str, Field(strict=True)]
    ocorrido_em: Annotated[str, Field(strict=True)]
    dados: Dict[str, Any]


def _dados_invalidos():
    return ErroAPI(422, "DADOS_INVALIDOS", "Dados do evento invalidos.")


def _exigir(dados: dict, campos) -> None:
    for c in campos:
        if c not in dados:
            raise _dados_invalidos()


_trava = threading.RLock()
_rastreamentos: dict = {}
_eventos_processados: set = set()

STATUS_FINAIS = {"ENTREGUE", "CANCELADO", "DEVOLVIDO"}


@app.post("/admin/reset", status_code=204)
def reset():
    with _trava:
        _rastreamentos.clear()
        _eventos_processados.clear()
    return Response(status_code=204)


@app.post("/eventos")
def receber_evento(evento: Evento):
    with _trava:
        if evento.id in _eventos_processados:
            return {"processado": False}
        dados = evento.dados
        if evento.tipo == "envio.criado":
            _exigir(dados, ["envio_id", "codigo_rastreio", "pedido_id", "transportadora_id",
                            "data_prevista_entrega", "data_hora"])
            _rastreamentos[dados["codigo_rastreio"]] = {
                "codigo_rastreio": dados["codigo_rastreio"],
                "envio_id": dados["envio_id"],
                "pedido_id": dados["pedido_id"],
                "transportadora_id": dados["transportadora_id"],
                "data_prevista_entrega": dados["data_prevista_entrega"],
                "historico": [{"status": "CRIADO", "data_hora": dados["data_hora"], "local": None}],
            }
            _eventos_processados.add(evento.id)
            return {"processado": True}
        if evento.tipo == "envio.status_atualizado":
            _exigir(dados, ["codigo_rastreio", "status", "data_hora"])
            r = _rastreamentos.get(dados["codigo_rastreio"])
            if r is None:
                return {"processado": False}
            r["historico"].append({
                "status": dados["status"],
                "data_hora": dados["data_hora"],
                "local": dados.get("local"),
            })
            _eventos_processados.add(evento.id)
            return {"processado": True}
        return {"processado": False}


@app.get("/rastreamento/{codigo_rastreio}")
def obter_rastreamento(codigo_rastreio: str, data: Optional[str] = Query(None)):
    if data is not None and not _data_valida(data):
        raise ErroAPI(422, "DADOS_INVALIDOS", "Data mal formada.")
    with _trava:
        r = _rastreamentos.get(codigo_rastreio)
        if r is None:
            raise ErroAPI(404, "RASTREIO_NAO_ENCONTRADO", "Codigo de rastreio nao encontrado.")
        historico = copy.deepcopy(r["historico"])
        status = historico[-1]["status"]
        prevista = r["data_prevista_entrega"]
        atrasado = False
        if data is not None and status not in STATUS_FINAIS and isinstance(prevista, str):
            atrasado = data > prevista
        return {
            "codigo_rastreio": r["codigo_rastreio"],
            "envio_id": r["envio_id"],
            "pedido_id": r["pedido_id"],
            "transportadora_id": r["transportadora_id"],
            "data_prevista_entrega": prevista,
            "status": status,
            "entregue": status == "ENTREGUE",
            "atrasado": atrasado,
            "historico": historico,
        }
