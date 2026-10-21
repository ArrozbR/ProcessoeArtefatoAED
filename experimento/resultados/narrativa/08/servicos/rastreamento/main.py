# arquivo: servicos/rastreamento/main.py
import copy
import json
import re
import threading
from datetime import date
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
async def _tratar_erro_api(request, exc: ErroAPI):
    return JSONResponse(status_code=exc.status, content={"erro": exc.codigo, "mensagem": exc.mensagem})


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request, exc):
    return JSONResponse(status_code=422, content={"erro": "DADOS_INVALIDOS", "mensagem": "Dados inválidos."})


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request, exc: StarletteHTTPException):
    codigos = {404: "NAO_ENCONTRADO", 405: "METODO_NAO_PERMITIDO"}
    return JSONResponse(
        status_code=exc.status_code,
        content={"erro": codigos.get(exc.status_code, "ERRO"), "mensagem": str(exc.detail)},
    )


def _dados_invalidos():
    return ErroAPI(422, "DADOS_INVALIDOS", "Dados inválidos.")


_lock = threading.Lock()
_rastreamentos = {}
_processados = set()


def _chave(valor) -> str:
    return json.dumps(valor, sort_keys=True, ensure_ascii=False)


@app.post("/admin/reset", status_code=204)
def reset():
    with _lock:
        _rastreamentos.clear()
        _processados.clear()
    return Response(status_code=204)


@app.post("/eventos")
async def receber_evento(request: Request):
    try:
        evento = await request.json()
    except Exception:
        raise _dados_invalidos()
    if not isinstance(evento, dict):
        raise _dados_invalidos()
    for campo in ("id", "tipo", "ocorrido_em", "dados"):
        if campo not in evento:
            raise _dados_invalidos()
    dados = evento["dados"]
    if not isinstance(dados, dict):
        raise _dados_invalidos()

    chave = _chave(evento["id"])
    tipo = evento["tipo"]
    with _lock:
        if chave in _processados:
            return {"processado": False}
        if tipo == "envio.criado":
            codigo = dados.get("codigo_rastreio")
            if not isinstance(codigo, str):
                return {"processado": False}
            _rastreamentos[codigo] = {
                "codigo_rastreio": codigo,
                "envio_id": dados.get("envio_id"),
                "pedido_id": dados.get("pedido_id"),
                "transportadora_id": dados.get("transportadora_id"),
                "data_prevista_entrega": dados.get("data_prevista_entrega"),
                "historico": [{"status": "CRIADO", "data_hora": dados.get("data_hora"), "local": None}],
            }
            _processados.add(chave)
            return {"processado": True}
        if tipo == "envio.status_atualizado":
            codigo = dados.get("codigo_rastreio")
            r = _rastreamentos.get(codigo) if isinstance(codigo, str) else None
            if r is None:
                return {"processado": False}
            r["historico"].append({
                "status": dados.get("status"),
                "data_hora": dados.get("data_hora"),
                "local": dados.get("local"),
            })
            _processados.add(chave)
            return {"processado": True}
        return {"processado": False}


@app.get("/rastreamento/{codigo_rastreio}")
def obter_rastreamento(codigo_rastreio: str, data: Optional[str] = None):
    data_ref = None
    if data is not None:
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", data):
            raise _dados_invalidos()
        try:
            data_ref = date.fromisoformat(data)
        except ValueError:
            raise _dados_invalidos()

    with _lock:
        r = _rastreamentos.get(codigo_rastreio)
        if r is None:
            raise ErroAPI(404, "RASTREIO_NAO_ENCONTRADO", "Rastreio não encontrado.")
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
