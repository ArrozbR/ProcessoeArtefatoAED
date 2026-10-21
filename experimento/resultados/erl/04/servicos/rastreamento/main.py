# arquivo: servicos/rastreamento/main.py
import copy
import re
import threading
from datetime import date
from typing import Any, Optional

from fastapi import Body, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

RE_DATA = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
TERMINAIS = {"ENTREGUE", "CANCELADO", "DEVOLVIDO"}

_lock = threading.RLock()
_estado = {"processados": set(), "rastreios": {}}


def erro(status: int, codigo: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


@app.exception_handler(RequestValidationError)
async def _validacao(request: Request, exc: RequestValidationError):
    return erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return erro(404, "RECURSO_NAO_ENCONTRADO", "Rota não encontrada.")
    if exc.status_code == 405:
        return erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return erro(exc.status_code, "ERRO", str(exc.detail))


def _parse_data(v: Any) -> Optional[date]:
    if not isinstance(v, str) or not RE_DATA.match(v):
        return None
    try:
        return date.fromisoformat(v)
    except ValueError:
        return None


@app.post("/eventos")
def receber_evento(corpo: Any = Body(default=None)):
    if not isinstance(corpo, dict):
        return erro(422, "DADOS_INVALIDOS", "Envelope inválido.")
    for campo in ("id", "tipo", "ocorrido_em", "dados"):
        if campo not in corpo:
            return erro(422, "DADOS_INVALIDOS", "Envelope sem o campo %s." % campo)
    dados = corpo["dados"]
    if not isinstance(dados, dict):
        return erro(422, "DADOS_INVALIDOS", "Campo dados inválido.")
    evento_id = str(corpo["id"])
    tipo = corpo["tipo"]

    with _lock:
        if evento_id in _estado["processados"]:
            return JSONResponse(status_code=200, content={"processado": False})

        if tipo == "envio.criado":
            codigo = dados.get("codigo_rastreio")
            _estado["rastreios"][codigo] = {
                "codigo_rastreio": codigo,
                "envio_id": dados.get("envio_id"),
                "pedido_id": dados.get("pedido_id"),
                "transportadora_id": dados.get("transportadora_id"),
                "data_prevista_entrega": dados.get("data_prevista_entrega"),
                "historico": [
                    {"status": "CRIADO", "data_hora": dados.get("data_hora"), "local": None}
                ],
            }
            _estado["processados"].add(evento_id)
            return JSONResponse(status_code=200, content={"processado": True})

        if tipo == "envio.status_atualizado":
            codigo = dados.get("codigo_rastreio")
            r = _estado["rastreios"].get(codigo)
            if r is None:
                return JSONResponse(status_code=200, content={"processado": False})
            r["historico"].append(
                {
                    "status": dados.get("status"),
                    "data_hora": dados.get("data_hora"),
                    "local": dados.get("local"),
                }
            )
            _estado["processados"].add(evento_id)
            return JSONResponse(status_code=200, content={"processado": True})

    return JSONResponse(status_code=200, content={"processado": False})


@app.get("/rastreamento/{codigo_rastreio}")
def consultar(codigo_rastreio: str, data: Optional[str] = None):
    data_ref = None
    if data is not None:
        data_ref = _parse_data(data)
        if data_ref is None:
            return erro(422, "DADOS_INVALIDOS", "Parâmetro data inválido.")
    with _lock:
        r = _estado["rastreios"].get(codigo_rastreio)
        if r is None:
            return erro(404, "RASTREIO_NAO_ENCONTRADO", "Rastreio não encontrado.")
        historico = copy.deepcopy(r["historico"])
        status = historico[-1]["status"] if historico else None
        atrasado = False
        if data_ref is not None and status not in TERMINAIS:
            prevista = _parse_data(r["data_prevista_entrega"])
            if prevista is not None and data_ref > prevista:
                atrasado = True
        resposta = {
            "codigo_rastreio": r["codigo_rastreio"],
            "envio_id": r["envio_id"],
            "pedido_id": r["pedido_id"],
            "transportadora_id": r["transportadora_id"],
            "data_prevista_entrega": r["data_prevista_entrega"],
            "status": status,
            "entregue": status == "ENTREGUE",
            "atrasado": atrasado,
            "historico": historico,
        }
    return JSONResponse(status_code=200, content=resposta)


@app.post("/admin/reset")
def reset():
    with _lock:
        _estado["processados"].clear()
        _estado["rastreios"].clear()
    return Response(status_code=204)
