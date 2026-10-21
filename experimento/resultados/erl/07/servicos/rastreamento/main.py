# arquivo: servicos/rastreamento/main.py
import copy
import json
import re
from datetime import datetime

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

RE_DATA = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
TERMINAIS = {"ENTREGUE", "CANCELADO", "DEVOLVIDO"}

_estado = {"rastreamentos": {}, "processados": set()}


class ErroAPI(Exception):
    def __init__(self, status, erro, mensagem):
        self.status = status
        self.erro = erro
        self.mensagem = mensagem


def _erro(status, erro, mensagem):
    return JSONResponse(status_code=status, content={"erro": erro, "mensagem": mensagem})


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request, exc):
    return _erro(exc.status, exc.erro, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request, exc):
    return _erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request, exc):
    if exc.status_code == 404:
        return _erro(404, "RECURSO_NAO_ENCONTRADO", "Recurso não encontrado.")
    if exc.status_code == 422:
        return _erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")
    return _erro(exc.status_code, "ERRO", str(exc.detail))


def _invalido(mensagem):
    raise ErroAPI(422, "DADOS_INVALIDOS", mensagem)


async def _ler_corpo(request):
    try:
        bruto = await request.body()
        corpo = json.loads(bruto)
    except Exception:
        _invalido("Corpo JSON inválido.")
    if not isinstance(corpo, dict):
        _invalido("O corpo deve ser um objeto JSON.")
    return corpo


@app.post("/admin/reset")
def reset():
    _estado["rastreamentos"].clear()
    _estado["processados"].clear()
    return Response(status_code=204)


@app.post("/eventos")
async def consumir(request: Request):
    corpo = await _ler_corpo(request)
    for campo in ("id", "tipo", "ocorrido_em", "dados"):
        if campo not in corpo:
            _invalido(f"Campo '{campo}' ausente no envelope.")
    evento_id = corpo["id"]
    tipo = corpo["tipo"]
    dados = corpo["dados"]
    if not isinstance(evento_id, str) or not isinstance(tipo, str) or not isinstance(dados, dict):
        _invalido("Envelope inválido.")

    if evento_id in _estado["processados"]:
        return JSONResponse(status_code=200, content={"processado": False})

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
        _estado["processados"].add(evento_id)
        return JSONResponse(status_code=200, content={"processado": True})

    if tipo == "envio.status_atualizado":
        codigo = dados.get("codigo_rastreio")
        rastreamento = _estado["rastreamentos"].get(codigo)
        if rastreamento is None:
            return JSONResponse(status_code=200, content={"processado": False})
        rastreamento["historico"].append({
            "status": dados.get("status"),
            "data_hora": dados.get("data_hora"),
            "local": dados.get("local"),
        })
        _estado["processados"].add(evento_id)
        return JSONResponse(status_code=200, content={"processado": True})

    return JSONResponse(status_code=200, content={"processado": False})


@app.get("/rastreamento/{codigo}")
def consultar(codigo: str, request: Request):
    data_txt = request.query_params.get("data")
    data = None
    if data_txt is not None:
        if not RE_DATA.fullmatch(data_txt):
            _invalido("Parâmetro 'data' inválido.")
        try:
            data = datetime.strptime(data_txt, "%Y-%m-%d").date()
        except ValueError:
            _invalido("Parâmetro 'data' inválido.")

    rastreamento = _estado["rastreamentos"].get(codigo)
    if rastreamento is None:
        raise ErroAPI(404, "RASTREIO_NAO_ENCONTRADO", "Rastreio não encontrado.")

    status = rastreamento["historico"][-1]["status"]
    atrasado = False
    if data is not None and status not in TERMINAIS:
        try:
            prevista = datetime.strptime(rastreamento["data_prevista_entrega"], "%Y-%m-%d").date()
            atrasado = data > prevista
        except Exception:
            atrasado = False

    resposta = {
        "codigo_rastreio": rastreamento["codigo_rastreio"],
        "envio_id": rastreamento["envio_id"],
        "pedido_id": rastreamento["pedido_id"],
        "transportadora_id": rastreamento["transportadora_id"],
        "data_prevista_entrega": rastreamento["data_prevista_entrega"],
        "status": status,
        "entregue": status == "ENTREGUE",
        "atrasado": atrasado,
        "historico": copy.deepcopy(rastreamento["historico"]),
    }
    return JSONResponse(status_code=200, content=resposta)
