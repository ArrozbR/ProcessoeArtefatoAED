# arquivo: servicos/rastreamento/main.py
import copy
import re
from datetime import date

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

RE_DATA = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
ESTADOS_TERMINAIS = {"ENTREGUE", "CANCELADO", "DEVOLVIDO"}
TIPOS_CONHECIDOS = {"envio.criado", "envio.status_atualizado"}

_estado = {"processados": set(), "rastreamentos": {}}


class ErroAPI(Exception):
    def __init__(self, status: int, codigo: str, mensagem: str):
        self.status = status
        self.codigo = codigo
        self.mensagem = mensagem


def _resposta_erro(status: int, codigo: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request: Request, exc: ErroAPI):
    return _resposta_erro(exc.status, exc.codigo, exc.mensagem)


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


def _invalido(mensagem: str) -> ErroAPI:
    return ErroAPI(422, "DADOS_INVALIDOS", mensagem)


async def _ler_corpo(request: Request) -> dict:
    try:
        corpo = await request.json()
    except Exception:
        raise _invalido("Corpo da requisição ausente ou não é JSON válido.")
    if not isinstance(corpo, dict):
        raise _invalido("O corpo da requisição deve ser um objeto JSON.")
    return corpo


def _chave_evento(valor) -> str:
    if isinstance(valor, str):
        return valor
    return repr(valor)


@app.post("/eventos")
async def consumir_evento(request: Request):
    evento = await _ler_corpo(request)
    for campo in ("id", "tipo", "ocorrido_em", "dados"):
        if campo not in evento:
            raise _invalido(f"Envelope sem o campo '{campo}'.")

    chave = _chave_evento(evento["id"])
    if chave in _estado["processados"]:
        return JSONResponse(status_code=200, content={"processado": False})

    tipo = evento["tipo"]
    if tipo not in TIPOS_CONHECIDOS:
        return JSONResponse(status_code=200, content={"processado": False})

    dados = evento["dados"]
    if not isinstance(dados, dict):
        raise _invalido("Campo 'dados' deve ser um objeto.")

    if tipo == "envio.criado":
        try:
            rastreamento = {
                "codigo_rastreio": dados["codigo_rastreio"],
                "envio_id": dados["envio_id"],
                "pedido_id": dados["pedido_id"],
                "transportadora_id": dados["transportadora_id"],
                "data_prevista_entrega": dados["data_prevista_entrega"],
                "historico": [{"status": "CRIADO", "data_hora": dados["data_hora"], "local": None}],
            }
        except KeyError:
            raise _invalido("Dados do evento incompletos.")
        _estado["rastreamentos"][rastreamento["codigo_rastreio"]] = rastreamento
        _estado["processados"].add(chave)
        return JSONResponse(status_code=200, content={"processado": True})

    # envio.status_atualizado
    try:
        codigo = dados["codigo_rastreio"]
        item = {"status": dados["status"], "data_hora": dados["data_hora"], "local": dados.get("local")}
    except KeyError:
        raise _invalido("Dados do evento incompletos.")
    rastreamento = _estado["rastreamentos"].get(codigo) if isinstance(codigo, str) else None
    if rastreamento is None:
        return JSONResponse(status_code=200, content={"processado": False})
    rastreamento["historico"].append(item)
    _estado["processados"].add(chave)
    return JSONResponse(status_code=200, content={"processado": True})


@app.get("/rastreamento/{codigo_rastreio}")
async def consultar_rastreamento(codigo_rastreio: str, request: Request):
    data_consulta = None
    if "data" in request.query_params:
        texto = request.query_params["data"]
        if not RE_DATA.fullmatch(texto):
            raise _invalido("Parâmetro 'data' deve estar no formato AAAA-MM-DD.")
        try:
            data_consulta = date.fromisoformat(texto)
        except ValueError:
            raise _invalido("Parâmetro 'data' não é uma data válida.")

    rastreamento = _estado["rastreamentos"].get(codigo_rastreio)
    if rastreamento is None:
        raise ErroAPI(404, "RASTREIO_NAO_ENCONTRADO", "Rastreamento não encontrado.")

    status = rastreamento["historico"][-1]["status"]
    atrasado = False
    if data_consulta is not None and status not in ESTADOS_TERMINAIS:
        try:
            prevista = date.fromisoformat(rastreamento["data_prevista_entrega"])
            atrasado = data_consulta > prevista
        except (TypeError, ValueError):
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


@app.post("/admin/reset", status_code=204)
async def reset():
    _estado["processados"] = set()
    _estado["rastreamentos"] = {}
    return Response(status_code=204)
