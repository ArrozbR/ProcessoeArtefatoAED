# arquivo: servicos/rastreamento/main.py
import copy
import json
import re
import threading
from datetime import date
from typing import Any

from fastapi import Body, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

DATA_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
TERMINAIS = {"ENTREGUE", "CANCELADO", "DEVOLVIDO"}
TIPOS_CONHECIDOS = {"envio.criado", "envio.status_atualizado"}

_lock = threading.Lock()
_rastreios: dict = {}
_processados: set = set()


def erro(status: int, codigo: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


@app.exception_handler(RequestValidationError)
async def _validacao(request: Request, exc: RequestValidationError):
    return erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return erro(404, "RECURSO_NAO_ENCONTRADO", "Recurso não encontrado.")
    if exc.status_code == 422:
        return erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")
    return erro(exc.status_code, "ERRO_HTTP", str(exc.detail))


def _parse_data(s: Any):
    if not isinstance(s, str) or not DATA_RE.match(s):
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def _chave(id_evento: Any) -> str:
    return json.dumps(id_evento, sort_keys=True, ensure_ascii=False)


@app.post("/eventos")
def receber_evento(corpo: Any = Body(default=None)):
    if not isinstance(corpo, dict):
        return erro(422, "DADOS_INVALIDOS", "Envelope inválido.")
    for campo in ("id", "tipo", "ocorrido_em", "dados"):
        if campo not in corpo:
            return erro(422, "DADOS_INVALIDOS", f"Campo {campo} ausente no envelope.")
    dados = corpo["dados"]
    if not isinstance(dados, dict):
        return erro(422, "DADOS_INVALIDOS", "Campo dados inválido.")
    chave = _chave(corpo["id"])
    tipo = corpo["tipo"]

    with _lock:
        if chave in _processados:
            return JSONResponse(status_code=200, content={"processado": False})
        if tipo not in TIPOS_CONHECIDOS:
            return JSONResponse(status_code=200, content={"processado": False})

        if tipo == "envio.criado":
            codigo = dados.get("codigo_rastreio")
            _rastreios[codigo] = {
                "codigo_rastreio": codigo,
                "envio_id": dados.get("envio_id"),
                "pedido_id": dados.get("pedido_id"),
                "transportadora_id": dados.get("transportadora_id"),
                "data_prevista_entrega": dados.get("data_prevista_entrega"),
                "historico": [
                    {"status": "CRIADO", "data_hora": dados.get("data_hora"), "local": None}
                ],
            }
            _processados.add(chave)
            return JSONResponse(status_code=200, content={"processado": True})

        codigo = dados.get("codigo_rastreio")
        r = _rastreios.get(codigo) if isinstance(codigo, str) else None
        if r is None:
            return JSONResponse(status_code=200, content={"processado": False})
        r["historico"].append(
            {
                "status": dados.get("status"),
                "data_hora": dados.get("data_hora"),
                "local": dados.get("local"),
            }
        )
        _processados.add(chave)
        return JSONResponse(status_code=200, content={"processado": True})


@app.get("/rastreamento/{codigo}")
def consultar(codigo: str, request: Request):
    data_consulta = None
    if "data" in request.query_params:
        data_consulta = _parse_data(request.query_params["data"])
        if data_consulta is None:
            return erro(422, "DADOS_INVALIDOS", "Parâmetro data inválido.")
    with _lock:
        r = _rastreios.get(codigo)
        if r is None:
            return erro(404, "RASTREIO_NAO_ENCONTRADO", "Rastreio não encontrado.")
        historico = copy.deepcopy(r["historico"])
        status = historico[-1]["status"] if historico else "CRIADO"
        atrasado = False
        if data_consulta is not None and status not in TERMINAIS:
            prevista = _parse_data(r["data_prevista_entrega"])
            if prevista is not None and data_consulta > prevista:
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
        _rastreios.clear()
        _processados.clear()
    return Response(status_code=204)
