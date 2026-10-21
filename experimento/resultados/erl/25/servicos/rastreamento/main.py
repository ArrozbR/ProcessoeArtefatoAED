# arquivo: servicos/rastreamento/main.py
import copy
import re
from datetime import date
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

DATA_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
TERMINAIS = {"ENTREGUE", "CANCELADO", "DEVOLVIDO"}

_estado: dict[str, Any] = {"processados": set(), "rastreios": {}}


def erro(status: int, codigo: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


def invalido(mensagem: str = "Dados inválidos.") -> JSONResponse:
    return erro(422, "DADOS_INVALIDOS", mensagem)


@app.exception_handler(RequestValidationError)
async def _validacao(request: Request, exc: RequestValidationError):
    return invalido("Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return erro(404, "RECURSO_NAO_ENCONTRADO", "Recurso não encontrado.")
    if exc.status_code == 405:
        return erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return erro(exc.status_code, "ERRO", str(exc.detail))


async def ler_json(request: Request) -> tuple[bool, Any]:
    try:
        return True, await request.json()
    except Exception:
        return False, None


def nao_processado() -> JSONResponse:
    return JSONResponse(status_code=200, content={"processado": False})


@app.post("/eventos")
async def eventos(request: Request):
    ok, env = await ler_json(request)
    if not ok or not isinstance(env, dict):
        return invalido("Envelope inválido.")
    for campo in ("id", "tipo", "ocorrido_em", "dados"):
        if campo not in env:
            return invalido(f"Campo '{campo}' ausente no envelope.")
    eid = env["id"]
    tipo = env["tipo"]
    dados = env["dados"]
    if not isinstance(dados, dict):
        return invalido("Campo 'dados' inválido.")
    try:
        chave = eid if isinstance(eid, str) else repr(eid)
    except Exception:
        return invalido("Campo 'id' inválido.")

    if chave in _estado["processados"]:
        return nao_processado()

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
    elif tipo == "envio.status_atualizado":
        codigo = dados.get("codigo_rastreio")
        r = _estado["rastreios"].get(codigo)
        if r is None:
            return nao_processado()
        r["historico"].append({
            "status": dados.get("status"),
            "data_hora": dados.get("data_hora"),
            "local": dados.get("local"),
        })
    else:
        return nao_processado()

    _estado["processados"].add(chave)
    return JSONResponse(status_code=200, content={"processado": True})


@app.get("/rastreamento/{codigo}")
async def consultar(codigo: str, request: Request):
    data_param = request.query_params.get("data")
    data_ref = None
    if data_param is not None:
        if not DATA_RE.match(data_param):
            return invalido("Parâmetro 'data' inválido.")
        try:
            data_ref = date.fromisoformat(data_param)
        except ValueError:
            return invalido("Parâmetro 'data' inválido.")

    r = _estado["rastreios"].get(codigo)
    if r is None:
        return erro(404, "RASTREIO_NAO_ENCONTRADO", "Rastreio não encontrado.")

    status = r["historico"][-1]["status"]
    atrasado = False
    if data_ref is not None and status not in TERMINAIS:
        try:
            prevista = date.fromisoformat(r["data_prevista_entrega"])
            atrasado = data_ref > prevista
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


@app.post("/admin/reset")
async def reset():
    _estado["processados"] = set()
    _estado["rastreios"] = {}
    return Response(status_code=204)
