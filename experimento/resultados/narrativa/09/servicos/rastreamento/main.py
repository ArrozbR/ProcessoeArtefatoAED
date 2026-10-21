# arquivo: servicos/rastreamento/main.py
import copy
import re
import threading
from datetime import datetime
from typing import Annotated, Any, Dict, Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()


class ErroAPI(Exception):
    def __init__(self, status: int, codigo: str, mensagem: str):
        super().__init__(mensagem)
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
    return _erro(422, "DADOS_INVALIDOS", "Dados invalidos na requisicao.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request: Request, exc: StarletteHTTPException):
    codigos = {404: "NAO_ENCONTRADO", 405: "METODO_NAO_PERMITIDO"}
    return _erro(exc.status_code, codigos.get(exc.status_code, "ERRO"), str(exc.detail))


class Evento(BaseModel):
    id: Annotated[str, Field(strict=True)]
    tipo: Annotated[str, Field(strict=True)]
    ocorrido_em: Annotated[str, Field(strict=True)]
    dados: Dict[str, Any]


_RE_DATA = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")

_trava = threading.RLock()
_rastreamentos = {}
_processados = set()


@app.post("/admin/reset", status_code=204)
def reset():
    with _trava:
        _rastreamentos.clear()
        _processados.clear()
    return Response(status_code=204)


def _exigir(dados: dict, campos):
    for c in campos:
        if c not in dados:
            raise ErroAPI(422, "DADOS_INVALIDOS", "Campo ausente em dados: %s." % c)


@app.post("/eventos")
def receber_evento(evento: Evento):
    with _trava:
        if evento.id in _processados:
            return {"processado": False}
        d = evento.dados
        if evento.tipo == "envio.criado":
            _exigir(d, ["envio_id", "codigo_rastreio", "pedido_id", "transportadora_id",
                        "data_prevista_entrega", "data_hora"])
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
            _exigir(d, ["codigo_rastreio", "status", "data_hora"])
            r = _rastreamentos.get(d["codigo_rastreio"])
            if r is None:
                return {"processado": False}
            r["historico"].append({"status": d["status"], "data_hora": d["data_hora"], "local": d.get("local")})
            _processados.add(evento.id)
            return {"processado": True}
        return {"processado": False}


@app.get("/rastreamento/{codigo_rastreio}")
def obter_rastreamento(codigo_rastreio: str, data: Optional[str] = None):
    if data is not None:
        valido = bool(_RE_DATA.match(data))
        if valido:
            try:
                datetime.strptime(data, "%Y-%m-%d")
            except ValueError:
                valido = False
        if not valido:
            raise ErroAPI(422, "DADOS_INVALIDOS", "Data invalida.")
    with _trava:
        r = _rastreamentos.get(codigo_rastreio)
        if r is None:
            raise ErroAPI(404, "RASTREIO_NAO_ENCONTRADO", "Rastreio nao encontrado.")
        status = r["historico"][-1]["status"]
        atrasado = False
        if data is not None and status not in ("ENTREGUE", "CANCELADO", "DEVOLVIDO"):
            atrasado = data > str(r["data_prevista_entrega"])
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
