# arquivo: servicos/rastreamento/main.py
import copy
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


@app.exception_handler(RequestValidationError)
async def _validacao(request, exc):
    return erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _http(request, exc):
    if exc.status_code == 404:
        return erro(404, "NAO_ENCONTRADO", "Recurso não encontrado.")
    if exc.status_code == 405:
        return erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return erro(exc.status_code, "ERRO", str(exc.detail))


def resposta_processado(valor):
    return JSONResponse(status_code=200, content={"processado": valor})


@app.post("/eventos")
async def eventos(request: Request):
    try:
        env = await request.json()
    except Exception:
        return erro(422, "DADOS_INVALIDOS", "Envelope inválido.")
    if not isinstance(env, dict):
        return erro(422, "DADOS_INVALIDOS", "Envelope inválido.")
    for campo in ("id", "tipo", "ocorrido_em", "dados"):
        if campo not in env:
            return erro(422, "DADOS_INVALIDOS", "Envelope sem o campo " + campo + ".")
    eid = env["id"]
    dados = env["dados"]
    if not isinstance(eid, str) or not isinstance(dados, dict):
        return erro(422, "DADOS_INVALIDOS", "Envelope inválido.")

    if eid in _estado["processados"]:
        return resposta_processado(False)

    tipo = env["tipo"]
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
        return resposta_processado(True)

    if tipo == "envio.status_atualizado":
        codigo = dados.get("codigo_rastreio")
        r = _estado["rastreios"].get(codigo)
        if r is None:
            return resposta_processado(False)
        r["historico"].append({
            "status": dados.get("status"),
            "data_hora": dados.get("data_hora"),
            "local": dados.get("local"),
        })
        _estado["processados"].add(eid)
        return resposta_processado(True)

    return resposta_processado(False)


@app.get("/rastreamento/{codigo}")
async def consultar(codigo: str, request: Request):
    data_txt = request.query_params.get("data")
    data = None
    if data_txt is not None:
        if not DATA_RE.match(data_txt):
            return erro(422, "DADOS_INVALIDOS", "Parâmetro data inválido.")
        try:
            data = date.fromisoformat(data_txt)
        except ValueError:
            return erro(422, "DADOS_INVALIDOS", "Parâmetro data inválido.")

    r = _estado["rastreios"].get(codigo)
    if r is None:
        return erro(404, "RASTREIO_NAO_ENCONTRADO", "Rastreamento não encontrado.")

    status = r["historico"][-1]["status"]
    atrasado = False
    if data is not None and status not in TERMINAIS:
        try:
            prevista = date.fromisoformat(r["data_prevista_entrega"])
            atrasado = data > prevista
        except Exception:
            atrasado = False

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
