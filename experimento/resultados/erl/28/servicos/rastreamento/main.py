# arquivo: servicos/rastreamento/main.py
import copy
import re
import threading
from datetime import date
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

RE_DATA = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
TERMINAIS = {"ENTREGUE", "CANCELADO", "DEVOLVIDO"}
CAMPOS_ENVELOPE = ("id", "tipo", "ocorrido_em", "dados")
CAMPOS_CRIADO = ("envio_id", "codigo_rastreio", "pedido_id", "transportadora_id", "data_prevista_entrega", "data_hora")
CAMPOS_STATUS = ("codigo_rastreio", "status", "data_hora", "local")

_lock = threading.Lock()
_rastreamentos: dict = {}
_eventos_processados: set = set()


class ErroAPI(Exception):
    def __init__(self, status: int, erro: str, mensagem: str):
        self.status = status
        self.erro = erro
        self.mensagem = mensagem


def _resposta_erro(status: int, erro: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": erro, "mensagem": mensagem})


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request: Request, exc: ErroAPI):
    return _resposta_erro(exc.status, exc.erro, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request: Request, exc: RequestValidationError):
    return _resposta_erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request: Request, exc: StarletteHTTPException):
    codigos = {404: "NAO_ENCONTRADO", 405: "METODO_NAO_PERMITIDO"}
    return _resposta_erro(exc.status_code, codigos.get(exc.status_code, "ERRO"), str(exc.detail))


def _invalido(mensagem: str = "Dados inválidos na requisição.") -> ErroAPI:
    return ErroAPI(422, "DADOS_INVALIDOS", mensagem)


async def _ler_corpo(request: Request) -> dict:
    try:
        corpo = await request.json()
    except Exception:
        raise _invalido("Corpo JSON ausente ou inválido.")
    if not isinstance(corpo, dict):
        raise _invalido("O corpo deve ser um objeto JSON.")
    return corpo


def _chave_evento(eid) -> str:
    return eid if isinstance(eid, str) else repr(eid)


@app.post("/eventos")
async def consumir_evento(request: Request):
    envelope = await _ler_corpo(request)
    for campo in CAMPOS_ENVELOPE:
        if campo not in envelope:
            raise _invalido(f"Envelope sem o campo '{campo}'.")
    chave = _chave_evento(envelope["id"])
    tipo = envelope["tipo"]
    dados = envelope["dados"]

    with _lock:
        if chave in _eventos_processados:
            return JSONResponse(status_code=200, content={"processado": False})
        if tipo not in ("envio.criado", "envio.status_atualizado"):
            return JSONResponse(status_code=200, content={"processado": False})
        if not isinstance(dados, dict):
            raise _invalido("Campo 'dados' inválido.")

        if tipo == "envio.criado":
            for campo in CAMPOS_CRIADO:
                if campo not in dados:
                    raise _invalido(f"Dados do evento sem o campo '{campo}'.")
            _rastreamentos[dados["codigo_rastreio"]] = {
                "codigo_rastreio": dados["codigo_rastreio"],
                "envio_id": dados["envio_id"],
                "pedido_id": dados["pedido_id"],
                "transportadora_id": dados["transportadora_id"],
                "data_prevista_entrega": dados["data_prevista_entrega"],
                "historico": [{"status": "CRIADO", "data_hora": dados["data_hora"], "local": None}],
            }
        else:
            for campo in CAMPOS_STATUS:
                if campo not in dados:
                    raise _invalido(f"Dados do evento sem o campo '{campo}'.")
            r = _rastreamentos.get(dados["codigo_rastreio"])
            if r is None:
                return JSONResponse(status_code=200, content={"processado": False})
            r["historico"].append({
                "status": dados["status"],
                "data_hora": dados["data_hora"],
                "local": dados["local"],
            })

        _eventos_processados.add(chave)
    return JSONResponse(status_code=200, content={"processado": True})


@app.get("/rastreamento/{codigo_rastreio}")
async def consultar_rastreamento(codigo_rastreio: str, data: Optional[str] = None):
    data_consulta = None
    if data is not None:
        if not RE_DATA.fullmatch(data):
            raise _invalido("Parâmetro 'data' inválido.")
        try:
            data_consulta = date.fromisoformat(data)
        except ValueError:
            raise _invalido("Parâmetro 'data' inválido.")

    with _lock:
        r = _rastreamentos.get(codigo_rastreio)
        if r is None:
            raise ErroAPI(404, "RASTREIO_NAO_ENCONTRADO", "Rastreamento não encontrado.")
        r = copy.deepcopy(r)

    status = r["historico"][-1]["status"] if r["historico"] else "CRIADO"
    atrasado = False
    if data_consulta is not None and status not in TERMINAIS:
        try:
            prevista = date.fromisoformat(r["data_prevista_entrega"])
            atrasado = data_consulta > prevista
        except Exception:
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
        "historico": r["historico"],
    }
    return JSONResponse(status_code=200, content=resposta)


@app.post("/admin/reset")
async def reset():
    with _lock:
        _rastreamentos.clear()
        _eventos_processados.clear()
    return Response(status_code=204)
