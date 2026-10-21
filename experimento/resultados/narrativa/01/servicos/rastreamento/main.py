# arquivo: servicos/rastreamento/main.py
import copy
import json
import re
import threading
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
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
        content={"erro": codigos.get(exc.status_code, "ERRO"), "mensagem": str(exc.detail)},
    )


_RE_DATA = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _data_valida(v: str) -> bool:
    if not _RE_DATA.match(v):
        return False
    try:
        datetime.strptime(v, "%Y-%m-%d")
    except ValueError:
        return False
    return True


def _invalido():
    return ErroAPI(422, "DADOS_INVALIDOS", "Dados inválidos.")


_lock = threading.Lock()
_rastreamentos: dict = {}
_eventos_processados: set = set()


@app.post("/admin/reset", status_code=204)
def reset():
    with _lock:
        _rastreamentos.clear()
        _eventos_processados.clear()
    return Response(status_code=204)


@app.post("/eventos")
async def receber_evento(request: Request):
    corpo = await request.body()
    try:
        evento = json.loads(corpo)
    except Exception:
        raise _invalido()
    if not isinstance(evento, dict):
        raise _invalido()
    for campo in ("id", "tipo", "ocorrido_em", "dados"):
        if campo not in evento or evento[campo] is None:
            raise _invalido()
    dados = evento["dados"]
    if not isinstance(dados, dict):
        raise _invalido()

    evento_id = evento["id"]
    tipo = evento["tipo"]
    with _lock:
        if not isinstance(evento_id, (str, int)) or evento_id in _eventos_processados:
            return {"processado": False}

        if tipo == "envio.criado":
            codigo = dados.get("codigo_rastreio")
            if codigo is None:
                return {"processado": False}
            _rastreamentos[codigo] = {
                "codigo_rastreio": codigo,
                "envio_id": dados.get("envio_id"),
                "pedido_id": dados.get("pedido_id"),
                "transportadora_id": dados.get("transportadora_id"),
                "data_prevista_entrega": dados.get("data_prevista_entrega"),
                "historico": [
                    {"status": "CRIADO", "data_hora": dados.get("data_hora", evento["ocorrido_em"]), "local": None}
                ],
            }
            _eventos_processados.add(evento_id)
            return {"processado": True}

        if tipo == "envio.status_atualizado":
            codigo = dados.get("codigo_rastreio")
            r = _rastreamentos.get(codigo) if codigo is not None else None
            if r is None:
                return {"processado": False}
            r["historico"].append(
                {
                    "status": dados.get("status"),
                    "data_hora": dados.get("data_hora", evento["ocorrido_em"]),
                    "local": dados.get("local"),
                }
            )
            _eventos_processados.add(evento_id)
            return {"processado": True}

        return {"processado": False}


@app.get("/rastreamento/{codigo_rastreio}")
def obter_rastreamento(codigo_rastreio: str, data: Optional[str] = None):
    if data is not None and not _data_valida(data):
        raise _invalido()
    with _lock:
        r = _rastreamentos.get(codigo_rastreio)
        if r is None:
            raise ErroAPI(404, "RASTREIO_NAO_ENCONTRADO", "Rastreio não encontrado.")
        historico = copy.deepcopy(r["historico"])
    status = historico[-1]["status"]
    entregue = status == "ENTREGUE"
    atrasado = False
    if data is not None and status not in ("ENTREGUE", "CANCELADO", "DEVOLVIDO"):
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
        "historico": historico,
    }
