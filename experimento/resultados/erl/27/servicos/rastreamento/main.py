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

DATA_REGEX = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
TERMINAIS = {"ENTREGUE", "CANCELADO", "DEVOLVIDO"}

_estado = {"rastreamentos": {}, "eventos_processados": set()}


class ErroApi(Exception):
    def __init__(self, status: int, erro: str, mensagem: str):
        self.status = status
        self.erro = erro
        self.mensagem = mensagem


def _resposta_erro(status: int, erro: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": erro, "mensagem": mensagem})


@app.exception_handler(ErroApi)
async def _tratar_erro_api(request: Request, exc: ErroApi):
    return _resposta_erro(exc.status, exc.erro, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request: Request, exc: RequestValidationError):
    return _resposta_erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return _resposta_erro(404, "NAO_ENCONTRADO", "Rota não encontrada.")
    if exc.status_code == 405:
        return _resposta_erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return _resposta_erro(exc.status_code, "ERRO", str(exc.detail))


def _invalido(mensagem: str = "Dados inválidos na requisição.") -> ErroApi:
    return ErroApi(422, "DADOS_INVALIDOS", mensagem)


async def _ler_json(request: Request) -> dict:
    corpo = await request.body()
    try:
        dados = json.loads(corpo)
    except Exception:
        raise _invalido("Corpo da requisição não é um JSON válido.")
    if not isinstance(dados, dict):
        raise _invalido("Corpo da requisição deve ser um objeto JSON.")
    return dados


def _parse_data(valor):
    if not isinstance(valor, str) or not DATA_REGEX.match(valor):
        return None
    try:
        return date.fromisoformat(valor)
    except ValueError:
        return None


def _chave_evento(eid) -> str:
    return json.dumps(eid, sort_keys=True)


@app.post("/eventos")
async def consumir_evento(request: Request):
    envelope = await _ler_json(request)
    for c in ("id", "tipo", "ocorrido_em", "dados"):
        if c not in envelope:
            raise _invalido(f"Campo obrigatório ausente no envelope: {c}.")
    dados = envelope["dados"]
    if not isinstance(dados, dict):
        raise _invalido("Campo dados deve ser um objeto.")

    chave = _chave_evento(envelope["id"])
    if chave in _estado["eventos_processados"]:
        return JSONResponse(status_code=200, content={"processado": False})

    tipo = envelope["tipo"]
    if tipo == "envio.criado":
        for c in ("envio_id", "codigo_rastreio", "pedido_id", "transportadora_id",
                  "data_prevista_entrega", "data_hora"):
            if c not in dados:
                raise _invalido(f"Campo ausente em dados: {c}.")
        codigo = dados["codigo_rastreio"]
        _estado["rastreamentos"][codigo] = {
            "codigo_rastreio": codigo,
            "envio_id": dados["envio_id"],
            "pedido_id": dados["pedido_id"],
            "transportadora_id": dados["transportadora_id"],
            "data_prevista_entrega": dados["data_prevista_entrega"],
            "historico": [{"status": "CRIADO", "data_hora": dados["data_hora"], "local": None}],
        }
        _estado["eventos_processados"].add(chave)
        return JSONResponse(status_code=200, content={"processado": True})

    if tipo == "envio.status_atualizado":
        for c in ("codigo_rastreio", "status", "data_hora"):
            if c not in dados:
                raise _invalido(f"Campo ausente em dados: {c}.")
        codigo = dados["codigo_rastreio"]
        rastreamento = _estado["rastreamentos"].get(codigo) if isinstance(codigo, str) else None
        if rastreamento is None:
            return JSONResponse(status_code=200, content={"processado": False})
        rastreamento["historico"].append(
            {"status": dados["status"], "data_hora": dados["data_hora"], "local": dados.get("local")}
        )
        _estado["eventos_processados"].add(chave)
        return JSONResponse(status_code=200, content={"processado": True})

    return JSONResponse(status_code=200, content={"processado": False})


@app.get("/rastreamento/{codigo}")
async def consultar_rastreamento(codigo: str, request: Request):
    params = request.query_params
    data_consulta = None
    if "data" in params:
        data_consulta = _parse_data(params["data"])
        if data_consulta is None:
            raise _invalido("Parâmetro data inválido.")

    r = _estado["rastreamentos"].get(codigo)
    if r is None:
        raise ErroApi(404, "RASTREIO_NAO_ENCONTRADO", "Rastreamento não encontrado.")

    status = r["historico"][-1]["status"]
    atrasado = False
    if data_consulta is not None and status not in TERMINAIS:
        prevista = _parse_data(r["data_prevista_entrega"])
        if prevista is not None and data_consulta > prevista:
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
    _estado["rastreamentos"] = {}
    _estado["eventos_processados"] = set()
    return Response(status_code=204)
