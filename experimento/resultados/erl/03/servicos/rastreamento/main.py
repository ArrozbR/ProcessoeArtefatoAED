# arquivo: servicos/rastreamento/main.py
import copy
import re
import threading
from datetime import date

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

RE_DATA = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
ESTADOS_TERMINAIS = {"ENTREGUE", "CANCELADO", "DEVOLVIDO"}

_lock = threading.Lock()
_rastreamentos: dict = {}
_eventos_processados: set = set()


class ErroApi(Exception):
    def __init__(self, status: int, erro: str, mensagem: str):
        super().__init__(mensagem)
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
    return _resposta_erro(422, "DADOS_INVALIDOS", "Dados da requisição inválidos.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return _resposta_erro(404, "RECURSO_NAO_ENCONTRADO", "Recurso não encontrado.")
    if exc.status_code == 405:
        return _resposta_erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return _resposta_erro(exc.status_code, "ERRO_HTTP", str(exc.detail))


def _invalido(mensagem: str) -> ErroApi:
    return ErroApi(422, "DADOS_INVALIDOS", mensagem)


async def _ler_corpo(request: Request) -> dict:
    try:
        corpo = await request.json()
    except Exception:
        raise _invalido("Corpo da requisição ausente ou não é JSON válido.")
    if not isinstance(corpo, dict):
        raise _invalido("O corpo da requisição deve ser um objeto JSON.")
    return corpo


@app.post("/eventos")
async def consumir_evento(request: Request):
    corpo = await _ler_corpo(request)
    for campo in ("id", "tipo", "ocorrido_em", "dados"):
        if campo not in corpo:
            raise _invalido(f"Envelope sem o campo '{campo}'.")
    if not isinstance(corpo["dados"], dict):
        raise _invalido("Campo 'dados' deve ser um objeto.")

    evento_id = corpo["id"]
    tipo = corpo["tipo"]
    dados = corpo["dados"]

    with _lock:
        if not isinstance(evento_id, (str, int)) or isinstance(evento_id, bool):
            raise _invalido("Campo 'id' inválido.")
        if evento_id in _eventos_processados:
            return JSONResponse(status_code=200, content={"processado": False})

        if tipo == "envio.criado":
            codigo = dados.get("codigo_rastreio")
            _rastreamentos[codigo] = {
                "codigo_rastreio": codigo,
                "envio_id": dados.get("envio_id"),
                "pedido_id": dados.get("pedido_id"),
                "transportadora_id": dados.get("transportadora_id"),
                "data_prevista_entrega": dados.get("data_prevista_entrega"),
                "historico": [{"status": "CRIADO", "data_hora": dados.get("data_hora"), "local": None}],
            }
            _eventos_processados.add(evento_id)
            return JSONResponse(status_code=200, content={"processado": True})

        if tipo == "envio.status_atualizado":
            codigo = dados.get("codigo_rastreio")
            rastreamento = _rastreamentos.get(codigo)
            if rastreamento is None:
                return JSONResponse(status_code=200, content={"processado": False})
            rastreamento["historico"].append({
                "status": dados.get("status"),
                "data_hora": dados.get("data_hora"),
                "local": dados.get("local"),
            })
            _eventos_processados.add(evento_id)
            return JSONResponse(status_code=200, content={"processado": True})

        return JSONResponse(status_code=200, content={"processado": False})


@app.get("/rastreamento/{codigo}")
async def consultar_rastreamento(codigo: str, request: Request):
    params = request.query_params
    data_consulta = None
    if "data" in params:
        valor = params["data"]
        if not RE_DATA.fullmatch(valor):
            raise _invalido("Parâmetro 'data' inválido; use AAAA-MM-DD.")
        try:
            data_consulta = date.fromisoformat(valor)
        except ValueError:
            raise _invalido("Parâmetro 'data' inválido; use AAAA-MM-DD.")

    with _lock:
        rastreamento = _rastreamentos.get(codigo)
        if rastreamento is None:
            raise ErroApi(404, "RASTREIO_NAO_ENCONTRADO", "Rastreamento não encontrado.")
        historico = copy.deepcopy(rastreamento["historico"])
        status = historico[-1]["status"]
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
            "historico": historico,
        }
    return JSONResponse(status_code=200, content=resposta)


@app.post("/admin/reset")
async def reset():
    with _lock:
        _rastreamentos.clear()
        _eventos_processados.clear()
    return Response(status_code=204)
