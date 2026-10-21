# arquivo: servicos/rastreamento/main.py
import copy
import json
import re
from datetime import date

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

DATA_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
TERMINAIS = {"ENTREGUE", "CANCELADO", "DEVOLVIDO"}

_estado = {"processados": set(), "rastreios": {}}


def erro(status, codigo, mensagem):
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


def invalido(mensagem="Dados inválidos."):
    return erro(422, "DADOS_INVALIDOS", mensagem)


@app.exception_handler(RequestValidationError)
async def _handler_validacao(request, exc):
    return invalido("Requisição com dados inválidos.")


@app.exception_handler(StarletteHTTPException)
async def _handler_http(request, exc):
    if exc.status_code == 404:
        return erro(404, "RECURSO_NAO_ENCONTRADO", "Recurso não encontrado.")
    if exc.status_code == 405:
        return erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return erro(exc.status_code, "ERRO", str(exc.detail))


async def ler_corpo(request):
    try:
        raw = await request.body()
        dados = json.loads(raw)
    except Exception:
        return None
    if not isinstance(dados, dict):
        return None
    return dados


def parse_data(v):
    if not isinstance(v, str) or not DATA_RE.match(v):
        return None
    try:
        return date.fromisoformat(v)
    except ValueError:
        return None


def chave_evento(eid):
    try:
        return json.dumps(eid, sort_keys=True)
    except Exception:
        return str(eid)


@app.post("/eventos")
async def eventos(request: Request):
    ev = await ler_corpo(request)
    if ev is None:
        return invalido("Corpo inválido.")
    for campo in ("id", "tipo", "ocorrido_em", "dados"):
        if campo not in ev:
            return invalido(f"Campo {campo} ausente.")
    dados = ev["dados"]
    if not isinstance(dados, dict):
        return invalido("Campo dados inválido.")

    chave = chave_evento(ev["id"])
    if chave in _estado["processados"]:
        return JSONResponse(status_code=200, content={"processado": False})

    tipo = ev["tipo"]
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
        _estado["processados"].add(chave)
        return JSONResponse(status_code=200, content={"processado": True})

    if tipo == "envio.status_atualizado":
        codigo = dados.get("codigo_rastreio")
        r = _estado["rastreios"].get(codigo) if isinstance(codigo, str) else None
        if r is None:
            return JSONResponse(status_code=200, content={"processado": False})
        r["historico"].append(
            {
                "status": dados.get("status"),
                "data_hora": dados.get("data_hora"),
                "local": dados.get("local"),
            }
        )
        _estado["processados"].add(chave)
        return JSONResponse(status_code=200, content={"processado": True})

    return JSONResponse(status_code=200, content={"processado": False})


@app.get("/rastreamento/{codigo}")
async def consultar(codigo: str, request: Request):
    data_ref = None
    if "data" in request.query_params:
        data_ref = parse_data(request.query_params["data"])
        if data_ref is None:
            return invalido("Parâmetro data inválido.")

    r = _estado["rastreios"].get(codigo)
    if r is None:
        return erro(404, "RASTREIO_NAO_ENCONTRADO", "Rastreio não encontrado.")

    status = r["historico"][-1]["status"]
    atrasado = False
    if data_ref is not None and status not in TERMINAIS:
        prevista = parse_data(r["data_prevista_entrega"])
        if prevista is not None and data_ref > prevista:
            atrasado = True

    corpo = {
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
    return JSONResponse(status_code=200, content=corpo)


@app.post("/admin/reset")
async def reset():
    _estado["processados"].clear()
    _estado["rastreios"].clear()
    return Response(status_code=204)
