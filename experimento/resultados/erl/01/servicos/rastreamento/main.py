# arquivo: servicos/rastreamento/main.py
import copy
import datetime
import re

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

RE_DATA = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
ESTADOS_TERMINAIS = {"ENTREGUE", "CANCELADO", "DEVOLVIDO"}

_estado = {"rastreamentos": {}, "eventos_processados": set()}


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
        return _resposta_erro(404, "ROTA_NAO_ENCONTRADA", "Rota não encontrada.")
    if exc.status_code == 405:
        return _resposta_erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return _resposta_erro(exc.status_code, "ERRO", str(exc.detail))


def _invalido(mensagem):
    return ErroAPI(422, "DADOS_INVALIDOS", mensagem)


async def _ler_corpo(request):
    try:
        corpo = await request.json()
    except Exception:
        raise _invalido("Corpo da requisição ausente ou não é JSON válido.")
    if not isinstance(corpo, dict):
        raise _invalido("O corpo da requisição deve ser um objeto JSON.")
    return corpo


def _chave_evento(ident):
    if isinstance(ident, (str, int, float, bool)) or ident is None:
        return repr(ident) if not isinstance(ident, str) else ident
    return repr(ident)


@app.post("/eventos")
async def consumir_evento(request: Request):
    corpo = await _ler_corpo(request)
    for campo in ("id", "tipo", "ocorrido_em", "dados"):
        if campo not in corpo:
            raise _invalido(f"Campo '{campo}' ausente no envelope.")
    dados = corpo["dados"]
    if not isinstance(dados, dict):
        raise _invalido("Campo 'dados' deve ser um objeto.")

    chave = _chave_evento(corpo["id"])
    if chave in _estado["eventos_processados"]:
        return JSONResponse(status_code=200, content={"processado": False})

    tipo = corpo["tipo"]
    if tipo == "envio.criado":
        codigo = dados.get("codigo_rastreio")
        if not isinstance(codigo, str):
            raise _invalido("Campo 'dados.codigo_rastreio' ausente ou inválido.")
        _estado["rastreamentos"][codigo] = {
            "codigo_rastreio": codigo,
            "envio_id": dados.get("envio_id"),
            "pedido_id": dados.get("pedido_id"),
            "transportadora_id": dados.get("transportadora_id"),
            "data_prevista_entrega": dados.get("data_prevista_entrega"),
            "historico": [{"status": "CRIADO", "data_hora": dados.get("data_hora"), "local": None}],
        }
        _estado["eventos_processados"].add(chave)
        return JSONResponse(status_code=200, content={"processado": True})

    if tipo == "envio.status_atualizado":
        codigo = dados.get("codigo_rastreio")
        rastreamento = _estado["rastreamentos"].get(codigo) if isinstance(codigo, str) else None
        if rastreamento is None:
            return JSONResponse(status_code=200, content={"processado": False})
        rastreamento["historico"].append({
            "status": dados.get("status"),
            "data_hora": dados.get("data_hora"),
            "local": dados.get("local"),
        })
        _estado["eventos_processados"].add(chave)
        return JSONResponse(status_code=200, content={"processado": True})

    return JSONResponse(status_code=200, content={"processado": False})


@app.get("/rastreamento/{codigo}")
async def consultar_rastreamento(codigo: str, request: Request):
    data = None
    if "data" in request.query_params:
        texto = request.query_params["data"]
        if not RE_DATA.fullmatch(texto):
            raise _invalido("Parâmetro 'data' deve estar no formato AAAA-MM-DD.")
        try:
            datetime.datetime.strptime(texto, "%Y-%m-%d")
        except ValueError:
            raise _invalido("Parâmetro 'data' deve ser uma data válida.")
        data = texto

    rastreamento = _estado["rastreamentos"].get(codigo)
    if rastreamento is None:
        raise ErroAPI(404, "RASTREIO_NAO_ENCONTRADO", "Rastreamento não encontrado.")

    status = rastreamento["historico"][-1]["status"]
    atrasado = False
    prevista = rastreamento["data_prevista_entrega"]
    if data is not None and status not in ESTADOS_TERMINAIS and isinstance(prevista, str):
        atrasado = data > prevista

    resposta = {
        "codigo_rastreio": rastreamento["codigo_rastreio"],
        "envio_id": rastreamento["envio_id"],
        "pedido_id": rastreamento["pedido_id"],
        "transportadora_id": rastreamento["transportadora_id"],
        "data_prevista_entrega": prevista,
        "status": status,
        "entregue": status == "ENTREGUE",
        "atrasado": atrasado,
        "historico": copy.deepcopy(rastreamento["historico"]),
    }
    return JSONResponse(status_code=200, content=resposta)


@app.post("/admin/reset")
async def reset():
    _estado["rastreamentos"] = {}
    _estado["eventos_processados"] = set()
    return Response(status_code=204)
