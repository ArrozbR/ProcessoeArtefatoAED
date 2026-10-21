# arquivo: servicos/rastreamento/main.py
import copy
import json
import re
import threading
from datetime import date

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

DATA_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
TERMINAIS = {"ENTREGUE", "CANCELADO", "DEVOLVIDO"}

_lock = threading.Lock()
_rastreamentos: dict = {}
_eventos_processados: set = set()


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
    if exc.status_code == 422:
        return _erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")
    return _erro(exc.status_code, "ERRO", str(exc.detail))


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


def _chave_evento(evento_id) -> str:
    return json.dumps(evento_id, sort_keys=True, ensure_ascii=False)


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
    tipo = corpo["tipo"]

    with _lock:
        if chave in _eventos_processados:
            return JSONResponse(status_code=200, content={"processado": False})

        if tipo == "envio.criado":
            codigo = dados.get("codigo_rastreio")
            if not isinstance(codigo, str):
                raise _invalido("Evento sem 'codigo_rastreio'.")
            _rastreamentos[codigo] = {
                "codigo_rastreio": codigo,
                "envio_id": dados.get("envio_id"),
                "pedido_id": dados.get("pedido_id"),
                "transportadora_id": dados.get("transportadora_id"),
                "data_prevista_entrega": dados.get("data_prevista_entrega"),
                "historico": [{
                    "status": "CRIADO",
                    "data_hora": dados.get("data_hora"),
                    "local": None,
                }],
            }
            _eventos_processados.add(chave)
            return JSONResponse(status_code=200, content={"processado": True})

        if tipo == "envio.status_atualizado":
            codigo = dados.get("codigo_rastreio")
            if not isinstance(codigo, str) or codigo not in _rastreamentos:
                return JSONResponse(status_code=200, content={"processado": False})
            _rastreamentos[codigo]["historico"].append({
                "status": dados.get("status"),
                "data_hora": dados.get("data_hora"),
                "local": dados.get("local"),
            })
            _eventos_processados.add(chave)
            return JSONResponse(status_code=200, content={"processado": True})

        return JSONResponse(status_code=200, content={"processado": False})


@app.get("/rastreamento/{codigo}")
async def consultar_rastreamento(codigo: str, request: Request):
    params = request.query_params
    data_consulta = None
    if "data" in params:
        valor = params["data"]
        if not DATA_RE.match(valor):
            raise _invalido("Parâmetro 'data' deve estar no formato AAAA-MM-DD.")
        try:
            data_consulta = date.fromisoformat(valor)
        except ValueError:
            raise _invalido("Parâmetro 'data' não é uma data válida.")

    with _lock:
        r = _rastreamentos.get(codigo)
        if r is None:
            raise ErroAPI(404, "RASTREIO_NAO_ENCONTRADO", f"Rastreio '{codigo}' não encontrado.")
        historico = copy.deepcopy(r["historico"])
        status = historico[-1]["status"] if historico else None
        atrasado = False
        if data_consulta is not None and status not in TERMINAIS:
            try:
                prevista = date.fromisoformat(r["data_prevista_entrega"])
                atrasado = data_consulta > prevista
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
            "historico": historico,
        }
    return JSONResponse(status_code=200, content=resposta)


@app.post("/admin/reset")
async def reset():
    with _lock:
        _rastreamentos.clear()
        _eventos_processados.clear()
    return Response(status_code=204)
