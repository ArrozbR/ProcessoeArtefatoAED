# arquivo: servicos/rastreamento/main.py
import copy
import datetime
import json
import re
import threading

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

DATA_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
TERMINAIS = {"ENTREGUE", "CANCELADO", "DEVOLVIDO"}
CAMPOS_ENVELOPE = ("id", "tipo", "ocorrido_em", "dados")

_lock = threading.Lock()
_rastreamentos = {}
_processados = set()


def erro(status, codigo, mensagem):
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


def dados_invalidos(mensagem="Dados inválidos."):
    return erro(422, "DADOS_INVALIDOS", mensagem)


@app.exception_handler(RequestValidationError)
async def _validacao(request, exc):
    return dados_invalidos("Requisição com dados inválidos.")


@app.exception_handler(StarletteHTTPException)
async def _http(request, exc):
    if exc.status_code == 404:
        return erro(404, "RECURSO_NAO_ENCONTRADO", "Recurso não encontrado.")
    if exc.status_code == 405:
        return erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return erro(exc.status_code, "ERRO", str(exc.detail))


async def ler_json(request):
    try:
        raw = await request.body()
        if not raw:
            return None, False
        return json.loads(raw), True
    except Exception:
        return None, False


def chave_evento(eid):
    return json.dumps(eid, sort_keys=True)


@app.post("/eventos")
async def eventos(request: Request):
    corpo, ok = await ler_json(request)
    if not ok or not isinstance(corpo, dict):
        return dados_invalidos("Envelope inválido.")
    for campo in CAMPOS_ENVELOPE:
        if campo not in corpo:
            return dados_invalidos("Envelope sem o campo %s." % campo)
    dados = corpo["dados"]
    if not isinstance(dados, dict):
        return dados_invalidos("Campo dados inválido.")

    chave = chave_evento(corpo["id"])
    tipo = corpo["tipo"]
    with _lock:
        if chave in _processados:
            return JSONResponse(status_code=200, content={"processado": False})
        if tipo == "envio.criado":
            codigo = dados.get("codigo_rastreio")
            if codigo is None:
                return JSONResponse(status_code=200, content={"processado": False})
            _rastreamentos[codigo] = {
                "codigo_rastreio": codigo,
                "envio_id": dados.get("envio_id"),
                "pedido_id": dados.get("pedido_id"),
                "transportadora_id": dados.get("transportadora_id"),
                "data_prevista_entrega": dados.get("data_prevista_entrega"),
                "historico": [{"status": "CRIADO", "data_hora": dados.get("data_hora"), "local": None}],
            }
            _processados.add(chave)
            return JSONResponse(status_code=200, content={"processado": True})
        if tipo == "envio.status_atualizado":
            codigo = dados.get("codigo_rastreio")
            r = _rastreamentos.get(codigo) if isinstance(codigo, str) else None
            if r is None:
                return JSONResponse(status_code=200, content={"processado": False})
            r["historico"].append({
                "status": dados.get("status"),
                "data_hora": dados.get("data_hora"),
                "local": dados.get("local"),
            })
            _processados.add(chave)
            return JSONResponse(status_code=200, content={"processado": True})
        return JSONResponse(status_code=200, content={"processado": False})


@app.get("/rastreamento/{codigo}")
async def consultar(codigo: str, request: Request):
    data = None
    if "data" in request.query_params:
        s = request.query_params["data"]
        if not DATA_RE.fullmatch(s):
            return dados_invalidos("Parâmetro data inválido.")
        try:
            data = datetime.datetime.strptime(s, "%Y-%m-%d").date()
        except ValueError:
            return dados_invalidos("Parâmetro data inválido.")

    with _lock:
        r = _rastreamentos.get(codigo)
        if r is None:
            return erro(404, "RASTREIO_NAO_ENCONTRADO", "Rastreio não encontrado.")
        r = copy.deepcopy(r)

    status = r["historico"][-1]["status"]
    atrasado = False
    if data is not None and status not in TERMINAIS:
        try:
            prevista = datetime.datetime.strptime(r["data_prevista_entrega"], "%Y-%m-%d").date()
            atrasado = data > prevista
        except Exception:
            atrasado = False

    return JSONResponse(status_code=200, content={
        "codigo_rastreio": r["codigo_rastreio"],
        "envio_id": r["envio_id"],
        "pedido_id": r["pedido_id"],
        "transportadora_id": r["transportadora_id"],
        "data_prevista_entrega": r["data_prevista_entrega"],
        "status": status,
        "entregue": status == "ENTREGUE",
        "atrasado": atrasado,
        "historico": r["historico"],
    })


@app.post("/admin/reset")
async def reset():
    with _lock:
        _rastreamentos.clear()
        _processados.clear()
    return Response(status_code=204)
