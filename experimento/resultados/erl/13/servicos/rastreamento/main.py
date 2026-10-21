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

RE_DATA = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
TERMINAIS = {"ENTREGUE", "CANCELADO", "DEVOLVIDO"}

_estado = {"rastreamentos": {}, "eventos": set()}


class ErroAPI(Exception):
    def __init__(self, status: int, erro: str, mensagem: str):
        self.status = status
        self.erro = erro
        self.mensagem = mensagem


def _erro(status: int, erro: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": erro, "mensagem": mensagem})


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request: Request, exc: ErroAPI):
    return _erro(exc.status, exc.erro, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request: Request, exc: RequestValidationError):
    return _erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return _erro(404, "RECURSO_NAO_ENCONTRADO", "Recurso não encontrado.")
    if exc.status_code == 405:
        return _erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return _erro(exc.status_code, "ERRO", str(exc.detail))


def _invalido(mensagem: str) -> ErroAPI:
    return ErroAPI(422, "DADOS_INVALIDOS", mensagem)


async def _ler_json(request: Request):
    try:
        corpo = await request.body()
        return json.loads(corpo)
    except Exception:
        raise _invalido("Corpo da requisição não é um JSON válido.")


def _exigir(dados: dict, campos) -> None:
    for c in campos:
        if c not in dados:
            raise _invalido(f"Campo 'dados.{c}' ausente.")


@app.post("/eventos")
async def consumir_evento(request: Request):
    evento = await _ler_json(request)
    if not isinstance(evento, dict):
        raise _invalido("Envelope deve ser um objeto JSON.")
    for campo in ("id", "tipo", "ocorrido_em", "dados"):
        if campo not in evento:
            raise _invalido(f"Campo '{campo}' ausente no envelope.")

    eid = evento["id"]
    tipo = evento["tipo"]
    dados = evento["dados"]

    try:
        if eid in _estado["eventos"]:
            return JSONResponse(status_code=200, content={"processado": False})
    except TypeError:
        raise _invalido("Campo 'id' inválido.")

    if tipo == "envio.criado":
        if not isinstance(dados, dict):
            raise _invalido("Campo 'dados' inválido.")
        _exigir(dados, ("envio_id", "codigo_rastreio", "pedido_id", "transportadora_id",
                        "data_prevista_entrega", "data_hora"))
        codigo = dados["codigo_rastreio"]
        _estado["rastreamentos"][codigo] = {
            "codigo_rastreio": codigo,
            "envio_id": dados["envio_id"],
            "pedido_id": dados["pedido_id"],
            "transportadora_id": dados["transportadora_id"],
            "data_prevista_entrega": dados["data_prevista_entrega"],
            "historico": [{"status": "CRIADO", "data_hora": dados["data_hora"], "local": None}],
        }
        _estado["eventos"].add(eid)
        return JSONResponse(status_code=200, content={"processado": True})

    if tipo == "envio.status_atualizado":
        if not isinstance(dados, dict):
            raise _invalido("Campo 'dados' inválido.")
        _exigir(dados, ("codigo_rastreio", "status", "data_hora"))
        codigo = dados["codigo_rastreio"]
        rast = _estado["rastreamentos"].get(codigo)
        if rast is None:
            return JSONResponse(status_code=200, content={"processado": False})
        rast["historico"].append({
            "status": dados["status"],
            "data_hora": dados["data_hora"],
            "local": dados.get("local"),
        })
        _estado["eventos"].add(eid)
        return JSONResponse(status_code=200, content={"processado": True})

    return JSONResponse(status_code=200, content={"processado": False})


@app.get("/rastreamento/{codigo}")
async def consultar(codigo: str, request: Request):
    data_txt = request.query_params.get("data")
    data_consulta = None
    if data_txt is not None:
        if not RE_DATA.fullmatch(data_txt):
            raise _invalido("Parâmetro 'data' inválido.")
        try:
            data_consulta = date.fromisoformat(data_txt)
        except ValueError:
            raise _invalido("Parâmetro 'data' inválido.")

    rast = _estado["rastreamentos"].get(codigo)
    if rast is None:
        raise ErroAPI(404, "RASTREIO_NAO_ENCONTRADO", "Rastreamento não encontrado.")

    status = rast["historico"][-1]["status"]
    atrasado = False
    if data_consulta is not None and status not in TERMINAIS:
        atrasado = data_consulta.isoformat() > rast["data_prevista_entrega"]

    resposta = {
        "codigo_rastreio": rast["codigo_rastreio"],
        "envio_id": rast["envio_id"],
        "pedido_id": rast["pedido_id"],
        "transportadora_id": rast["transportadora_id"],
        "data_prevista_entrega": rast["data_prevista_entrega"],
        "status": status,
        "entregue": status == "ENTREGUE",
        "atrasado": atrasado,
        "historico": copy.deepcopy(rast["historico"]),
    }
    return JSONResponse(status_code=200, content=resposta)


@app.post("/admin/reset", status_code=204)
async def reset():
    _estado["rastreamentos"] = {}
    _estado["eventos"] = set()
    return Response(status_code=204)
