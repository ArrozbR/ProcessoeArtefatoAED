# arquivo: servicos/rastreamento/main.py
import re
from datetime import date

from fastapi import FastAPI, Request
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
async def _tratar_validacao(request: Request, exc: RequestValidationError):
    return erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return erro(404, "RECURSO_NAO_ENCONTRADO", "Recurso não encontrado.")
    if exc.status_code == 405:
        return erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return erro(exc.status_code, "ERRO", str(exc.detail))


async def _ler_json(request: Request):
    try:
        return await request.json()
    except Exception:
        return None


def _parse_data(valor):
    if not isinstance(valor, str) or not RE_DATA.fullmatch(valor):
        return None
    try:
        return date.fromisoformat(valor)
    except ValueError:
        return None


@app.post("/eventos")
async def consumir(request: Request):
    env = await _ler_json(request)
    if (
        not isinstance(env, dict)
        or any(c not in env for c in ("id", "tipo", "ocorrido_em", "dados"))
        or not isinstance(env["dados"], dict)
    ):
        return erro(422, "DADOS_INVALIDOS", "Envelope de evento inválido.")

    eid = env["id"]
    try:
        hash(eid)
    except TypeError:
        return erro(422, "DADOS_INVALIDOS", "Identificador de evento inválido.")
    if eid in _estado["processados"]:
        return JSONResponse(status_code=200, content={"processado": False})

    tipo = env["tipo"]
    dados = env["dados"]
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
        _estado["processados"].add(eid)
        return JSONResponse(status_code=200, content={"processado": True})

    if tipo == "envio.status_atualizado":
        r = _estado["rastreamentos"].get(dados.get("codigo_rastreio"))
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
    data_param = request.query_params.get("data")
    data_ref = None
    if data_param is not None:
        data_ref = _parse_data(data_param)
        if data_ref is None:
            return erro(422, "DADOS_INVALIDOS", "Parâmetro data inválido.")

    r = _estado["rastreamentos"].get(codigo)
    if r is None:
        return erro(404, "RASTREIO_NAO_ENCONTRADO", "Rastreamento não encontrado.")

    status = r["historico"][-1]["status"]
    atrasado = False
    if data_ref is not None and status not in TERMINAIS:
        try:
            atrasado = data_ref > date.fromisoformat(r["data_prevista_entrega"])
        except (TypeError, ValueError):
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
    _estado["processados"].clear()
    _estado["rastreamentos"].clear()
    return Response(status_code=204)
