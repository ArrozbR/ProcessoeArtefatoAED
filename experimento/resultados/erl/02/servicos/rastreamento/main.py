# arquivo: servicos/rastreamento/main.py
import copy
import re
import threading
from datetime import datetime
from typing import Any, Optional

from fastapi import Body, FastAPI, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

DATA_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
TERMINAIS = {"ENTREGUE", "CANCELADO", "DEVOLVIDO"}

_lock = threading.Lock()
_rastreamentos: dict = {}
_eventos_processados: set = set()


def erro(status: int, codigo: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request, exc):
    return erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request, exc):
    codigos = {404: "RECURSO_NAO_ENCONTRADO", 405: "METODO_NAO_PERMITIDO"}
    return erro(exc.status_code, codigos.get(exc.status_code, "ERRO_HTTP"), str(exc.detail))


def _data_valida(s) -> bool:
    if not isinstance(s, str) or not DATA_RE.match(s):
        return False
    try:
        datetime.strptime(s, "%Y-%m-%d")
    except ValueError:
        return False
    return True


@app.post("/admin/reset", status_code=204)
def reset():
    with _lock:
        _rastreamentos.clear()
        _eventos_processados.clear()
    return Response(status_code=204)


@app.post("/eventos")
def receber_evento(payload: Any = Body(None)):
    if not isinstance(payload, dict):
        return erro(422, "DADOS_INVALIDOS", "Envelope inválido.")
    for campo in ("id", "tipo", "ocorrido_em", "dados"):
        if campo not in payload:
            return erro(422, "DADOS_INVALIDOS", f"Envelope sem o campo '{campo}'.")
    dados = payload["dados"]
    if not isinstance(dados, dict):
        return erro(422, "DADOS_INVALIDOS", "Campo 'dados' inválido.")
    evento_id = payload["id"]
    tipo = payload["tipo"]
    chave = evento_id if isinstance(evento_id, (str, int, float, bool)) or evento_id is None else repr(evento_id)

    with _lock:
        if chave in _eventos_processados:
            return JSONResponse(status_code=200, content={"processado": False})

        if tipo == "envio.criado":
            codigo = dados.get("codigo_rastreio")
            _rastreamentos[codigo] = {
                "codigo_rastreio": codigo,
                "envio_id": dados.get("envio_id"),
                "pedido_id": dados.get("pedido_id"),
                "transportadora_id": dados.get("transportadora_id"),
                "data_prevista_entrega": dados.get("data_prevista_entrega"),
                "historico": [{"status": "CRIADO", "data_hora": dados.get("data_hora"), "local": None}],
            }
            _eventos_processados.add(chave)
            return JSONResponse(status_code=200, content={"processado": True})

        if tipo == "envio.status_atualizado":
            codigo = dados.get("codigo_rastreio")
            r = _rastreamentos.get(codigo)
            if r is None:
                return JSONResponse(status_code=200, content={"processado": False})
            r["historico"].append({
                "status": dados.get("status"),
                "data_hora": dados.get("data_hora"),
                "local": dados.get("local"),
            })
            _eventos_processados.add(chave)
            return JSONResponse(status_code=200, content={"processado": True})

        return JSONResponse(status_code=200, content={"processado": False})


@app.get("/rastreamento/{codigo}")
def consultar(codigo: str, data: Optional[str] = Query(None)):
    if data is not None and not _data_valida(data):
        return erro(422, "DADOS_INVALIDOS", "Parâmetro 'data' inválido.")
    with _lock:
        r = _rastreamentos.get(codigo)
        if r is None:
            return erro(404, "RASTREIO_NAO_ENCONTRADO", "Rastreio não encontrado.")
        r = copy.deepcopy(r)
    status = r["historico"][-1]["status"]
    atrasado = False
    if data is not None:
        prevista = r["data_prevista_entrega"]
        atrasado = status not in TERMINAIS and isinstance(prevista, str) and data > prevista
    corpo = {
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
    return JSONResponse(status_code=200, content=corpo)
