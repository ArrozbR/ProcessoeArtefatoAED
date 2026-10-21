# arquivo: servicos/rastreamento/main.py
import copy
import datetime
import re

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

DATA_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
TERMINAIS = {"ENTREGUE", "CANCELADO", "DEVOLVIDO"}

_estado = {"rastreios": {}, "processados": set()}

_INVALIDO = object()


def _erro(status, codigo, mensagem):
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request, exc):
    return _erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request, exc):
    if exc.status_code == 404:
        return _erro(404, "NAO_ENCONTRADO", "Rota não encontrada.")
    if exc.status_code == 405:
        return _erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return _erro(exc.status_code, "ERRO", str(exc.detail))


async def _ler_json(request):
    try:
        return await request.json()
    except Exception:
        return _INVALIDO


def _ler_data(texto):
    if not isinstance(texto, str) or not DATA_RE.fullmatch(texto):
        return None
    try:
        return datetime.date.fromisoformat(texto)
    except ValueError:
        return None


def _chave_id(v):
    try:
        hash(v)
        return v
    except TypeError:
        return repr(v)


@app.post("/eventos")
async def eventos(request: Request):
    corpo = await _ler_json(request)
    if corpo is _INVALIDO or not isinstance(corpo, dict):
        return _erro(422, "DADOS_INVALIDOS", "Envelope inválido.")
    for campo in ("id", "tipo", "ocorrido_em", "dados"):
        if campo not in corpo:
            return _erro(422, "DADOS_INVALIDOS", f"Campo '{campo}' ausente.")
    dados = corpo["dados"]
    if not isinstance(dados, dict):
        return _erro(422, "DADOS_INVALIDOS", "Campo 'dados' inválido.")

    eid = _chave_id(corpo["id"])
    if eid in _estado["processados"]:
        return JSONResponse(status_code=200, content={"processado": False})

    tipo = corpo["tipo"]
    if tipo == "envio.criado":
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
    bruto = request.query_params.get("data")
    data = None
    if bruto is not None:
        data = _ler_data(bruto)
        if data is None:
            return _erro(422, "DADOS_INVALIDOS", "Parâmetro 'data' inválido.")
    r = _estado["rastreios"].get(codigo)
    if r is None:
        return _erro(404, "RASTREIO_NAO_ENCONTRADO", "Rastreio não encontrado.")
    status = r["historico"][-1]["status"]
    atrasado = False
    if data is not None and status not in TERMINAIS:
        prevista = _ler_data(r["data_prevista_entrega"])
        if prevista is not None and data > prevista:
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
        "historico": copy.deepcopy(r["historico"]),
    }
    return JSONResponse(status_code=200, content=resposta)


@app.post("/admin/reset")
async def reset():
    _estado["rastreios"] = {}
    _estado["processados"] = set()
    return Response(status_code=204)
