# arquivo: servicos/rastreamento/main.py
import copy
import datetime
import re

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

DATA_REGEX = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
ESTADOS_TERMINAIS = {"ENTREGUE", "CANCELADO", "DEVOLVIDO"}

_estado = {"rastreamentos": {}, "processados": set()}


class ErroAPI(Exception):
    def __init__(self, status, codigo, mensagem):
        super().__init__(mensagem)
        self.status = status
        self.codigo = codigo
        self.mensagem = mensagem


def _resposta_erro(status, codigo, mensagem):
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request, exc):
    return _resposta_erro(exc.status, exc.codigo, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request, exc):
    return _resposta_erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request, exc):
    if exc.status_code == 404:
        return _resposta_erro(404, "NAO_ENCONTRADO", "Rota não encontrada.")
    if exc.status_code == 422:
        return _resposta_erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")
    return _resposta_erro(exc.status_code, "ERRO", str(exc.detail))


def _invalido(mensagem):
    return ErroAPI(422, "DADOS_INVALIDOS", mensagem)


async def _ler_corpo(request):
    try:
        corpo = await request.json()
    except Exception:
        raise _invalido("Corpo da requisição não é um JSON válido.")
    if not isinstance(corpo, dict):
        raise _invalido("Corpo da requisição deve ser um objeto JSON.")
    return corpo


def _parse_data(texto):
    if not isinstance(texto, str) or not DATA_REGEX.match(texto):
        return None
    try:
        return datetime.date.fromisoformat(texto)
    except ValueError:
        return None


@app.post("/eventos")
async def receber_evento(request: Request):
    envelope = await _ler_corpo(request)
    for campo in ("id", "tipo", "ocorrido_em", "dados"):
        if campo not in envelope:
            raise _invalido(f"Envelope sem o campo '{campo}'.")
    dados = envelope["dados"]
    if not isinstance(dados, dict):
        raise _invalido("Campo 'dados' inválido.")
    id_evento = envelope["id"]
    try:
        chave = str(id_evento)
    except Exception:
        raise _invalido("Campo 'id' inválido.")

    if chave in _estado["processados"]:
        return JSONResponse(status_code=200, content={"processado": False})

    tipo = envelope["tipo"]
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
    elif tipo == "envio.status_atualizado":
        codigo = dados.get("codigo_rastreio")
        rastreamento = _estado["rastreamentos"].get(codigo)
        if rastreamento is None:
            return JSONResponse(status_code=200, content={"processado": False})
        rastreamento["historico"].append({
            "status": dados.get("status"),
            "data_hora": dados.get("data_hora"),
            "local": dados.get("local"),
        })
    else:
        return JSONResponse(status_code=200, content={"processado": False})

    _estado["processados"].add(chave)
    return JSONResponse(status_code=200, content={"processado": True})


@app.get("/rastreamento/{codigo_rastreio}")
async def consultar_rastreamento(codigo_rastreio: str, request: Request):
    data = None
    if "data" in request.query_params:
        data = _parse_data(request.query_params["data"])
        if data is None:
            raise _invalido("Parâmetro 'data' inválido.")

    rastreamento = _estado["rastreamentos"].get(codigo_rastreio)
    if rastreamento is None:
        raise ErroAPI(404, "RASTREIO_NAO_ENCONTRADO", "Rastreamento não encontrado.")

    historico = copy.deepcopy(rastreamento["historico"])
    status = historico[-1]["status"] if historico else None

    atrasado = False
    if data is not None and status not in ESTADOS_TERMINAIS:
        prevista = _parse_data(rastreamento["data_prevista_entrega"])
        if prevista is not None and data > prevista:
            atrasado = True

    return JSONResponse(status_code=200, content={
        "codigo_rastreio": rastreamento["codigo_rastreio"],
        "envio_id": rastreamento["envio_id"],
        "pedido_id": rastreamento["pedido_id"],
        "transportadora_id": rastreamento["transportadora_id"],
        "data_prevista_entrega": rastreamento["data_prevista_entrega"],
        "status": status,
        "entregue": status == "ENTREGUE",
        "atrasado": atrasado,
        "historico": historico,
    })


@app.post("/admin/reset")
async def reset():
    _estado["rastreamentos"] = {}
    _estado["processados"] = set()
    return Response(status_code=204)
