# arquivo: servicos/rastreamento/main.py
import re
from datetime import datetime

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

DATA_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
TERMINAIS = {"ENTREGUE", "CANCELADO", "DEVOLVIDO"}

_estado = {"processados": set(), "rastreios": {}}


def erro(status: int, codigo: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


def dados_invalidos(mensagem: str = "Dados inválidos.") -> JSONResponse:
    return erro(422, "DADOS_INVALIDOS", mensagem)


@app.exception_handler(RequestValidationError)
async def _handler_validacao(request: Request, exc: RequestValidationError):
    return dados_invalidos("Requisição com dados inválidos.")


@app.exception_handler(StarletteHTTPException)
async def _handler_http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return erro(404, "NAO_ENCONTRADO", "Recurso não encontrado.")
    if exc.status_code == 405:
        return erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return erro(exc.status_code, "ERRO", str(exc.detail))


def _data_valida(valor) -> bool:
    if not isinstance(valor, str) or not DATA_RE.fullmatch(valor):
        return False
    try:
        datetime.strptime(valor, "%Y-%m-%d")
    except ValueError:
        return False
    return True


@app.post("/eventos")
async def eventos(request: Request):
    try:
        corpo = await request.json()
    except Exception:
        return dados_invalidos("Corpo JSON inválido.")
    if not isinstance(corpo, dict):
        return dados_invalidos("Envelope inválido.")
    for campo in ("id", "tipo", "ocorrido_em", "dados"):
        if campo not in corpo:
            return dados_invalidos(f"Envelope sem o campo '{campo}'.")

    eid = corpo["id"]
    tipo = corpo["tipo"]
    dados = corpo["dados"]
    try:
        if eid in _estado["processados"]:
            return JSONResponse(status_code=200, content={"processado": False})
    except TypeError:
        return dados_invalidos("Campo 'id' inválido.")

    if tipo == "envio.criado":
        if not isinstance(dados, dict):
            return dados_invalidos("Campo 'dados' inválido.")
        codigo = dados.get("codigo_rastreio")
        _estado["rastreios"][codigo] = {
            "codigo_rastreio": codigo,
            "envio_id": dados.get("envio_id"),
            "pedido_id": dados.get("pedido_id"),
            "transportadora_id": dados.get("transportadora_id"),
            "data_prevista_entrega": dados.get("data_prevista_entrega"),
            "historico": [{"status": "CRIADO", "data_hora": dados.get("data_hora"), "local": None}],
        }
        _estado["processados"].add(eid)
        return JSONResponse(status_code=200, content={"processado": True})

    if tipo == "envio.status_atualizado":
        if not isinstance(dados, dict):
            return dados_invalidos("Campo 'dados' inválido.")
        codigo = dados.get("codigo_rastreio")
        r = _estado["rastreios"].get(codigo) if isinstance(codigo, str) else None
        if r is None:
            return JSONResponse(status_code=200, content={"processado": False})
        r["historico"].append({
            "status": dados.get("status"),
            "data_hora": dados.get("data_hora"),
            "local": dados.get("local"),
        })
        _estado["processados"].add(eid)
        return JSONResponse(status_code=200, content={"processado": True})

    return JSONResponse(status_code=200, content={"processado": False})


@app.get("/rastreamento/{codigo}")
async def consultar(codigo: str, request: Request):
    data = request.query_params.get("data")
    if data is not None and not _data_valida(data):
        return dados_invalidos("Parâmetro 'data' inválido.")
    r = _estado["rastreios"].get(codigo)
    if r is None:
        return erro(404, "RASTREIO_NAO_ENCONTRADO", "Rastreio não encontrado.")
    status = r["historico"][-1]["status"]
    atrasado = False
    if data is not None and status not in TERMINAIS:
        prevista = r["data_prevista_entrega"]
        if isinstance(prevista, str) and data > prevista:
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
        "historico": r["historico"],
    }
    return JSONResponse(status_code=200, content=resposta)


@app.post("/admin/reset")
async def reset():
    _estado["processados"] = set()
    _estado["rastreios"] = {}
    return Response(status_code=204)
