# arquivo: servicos/rastreamento/main.py
import copy
import re
from datetime import date
from typing import Any

from fastapi import Body, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

RE_DATA = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
TERMINAIS = {"ENTREGUE", "CANCELADO", "DEVOLVIDO"}

_estado = {"processados": set(), "rastreamentos": {}}


def erro(status: int, codigo: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


@app.exception_handler(RequestValidationError)
async def _validacao(request: Request, exc: RequestValidationError):
    return erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return erro(404, "RECURSO_NAO_ENCONTRADO", "Recurso não encontrado.")
    if exc.status_code == 405:
        return erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return erro(exc.status_code, "ERRO", str(exc.detail))


@app.post("/admin/reset")
def reset():
    _estado["processados"] = set()
    _estado["rastreamentos"] = {}
    return Response(status_code=204)


@app.post("/eventos")
def eventos(corpo: Any = Body(None)):
    if not isinstance(corpo, dict):
        return erro(422, "DADOS_INVALIDOS", "Envelope inválido.")
    for campo in ("id", "tipo", "ocorrido_em", "dados"):
        if campo not in corpo:
            return erro(422, "DADOS_INVALIDOS", "Envelope sem o campo %s." % campo)
    dados = corpo["dados"]
    if not isinstance(dados, dict):
        return erro(422, "DADOS_INVALIDOS", "Campo dados inválido.")

    evento_id = corpo["id"]
    try:
        chave = evento_id if isinstance(evento_id, str) else repr(evento_id)
    except Exception:
        chave = str(evento_id)
    if chave in _estado["processados"]:
        return JSONResponse(status_code=200, content={"processado": False})

    tipo = corpo["tipo"]
    if tipo == "envio.criado":
        codigo = dados.get("codigo_rastreio")
        _estado["rastreamentos"][codigo] = {
            "codigo_rastreio": codigo,
            "envio_id": dados.get("envio_id"),
            "pedido_id": dados.get("pedido_id"),
            "transportadora_id": dados.get("transportadora_id"),
            "data_prevista_entrega": dados.get("data_prevista_entrega"),
            "historico": [{"status": "CRIADO", "data_hora": dados.get("data_hora"), "local": None}],
        }
        _estado["processados"].add(chave)
        return JSONResponse(status_code=200, content={"processado": True})

    if tipo == "envio.status_atualizado":
        codigo = dados.get("codigo_rastreio")
        r = _estado["rastreamentos"].get(codigo)
        if r is None:
            return JSONResponse(status_code=200, content={"processado": False})
        r["historico"].append({
            "status": dados.get("status"),
            "data_hora": dados.get("data_hora"),
            "local": dados.get("local"),
        })
        _estado["processados"].add(chave)
        return JSONResponse(status_code=200, content={"processado": True})

    return JSONResponse(status_code=200, content={"processado": False})


@app.get("/rastreamento/{codigo}")
def consultar(codigo: str, request: Request):
    data_consulta = None
    if "data" in request.query_params:
        valor = request.query_params["data"]
        if not RE_DATA.fullmatch(valor):
            return erro(422, "DADOS_INVALIDOS", "Parâmetro data inválido.")
        try:
            data_consulta = date.fromisoformat(valor)
        except ValueError:
            return erro(422, "DADOS_INVALIDOS", "Parâmetro data inválido.")

    r = _estado["rastreamentos"].get(codigo)
    if r is None:
        return erro(404, "RASTREIO_NAO_ENCONTRADO", "Rastreio não encontrado.")

    status = r["historico"][-1]["status"]
    atrasado = False
    if data_consulta is not None and status not in TERMINAIS:
        try:
            prevista = date.fromisoformat(r["data_prevista_entrega"])
            atrasado = data_consulta > prevista
        except (TypeError, ValueError):
            atrasado = False

    resposta = {
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
    return JSONResponse(status_code=200, content=resposta)
