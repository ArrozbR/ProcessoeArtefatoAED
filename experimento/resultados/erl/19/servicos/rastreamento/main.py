# arquivo: servicos/rastreamento/main.py
import copy
import re
import threading
from datetime import date

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

DATA_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
TERMINAIS = {"ENTREGUE", "CANCELADO", "DEVOLVIDO"}
TIPOS_CONHECIDOS = {"envio.criado", "envio.status_atualizado"}


class ErroAPI(Exception):
    def __init__(self, status: int, erro: str, mensagem: str):
        super().__init__(mensagem)
        self.status = status
        self.erro = erro
        self.mensagem = mensagem


app = FastAPI()

_lock = threading.Lock()
_rastreamentos: dict = {}
_eventos_processados: set = set()


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
        return _erro(404, "NAO_ENCONTRADO", "Rota não encontrada.")
    if exc.status_code == 405:
        return _erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return _erro(exc.status_code, "ERRO", str(exc.detail))


def _invalido(mensagem: str) -> ErroAPI:
    return ErroAPI(422, "DADOS_INVALIDOS", mensagem)


async def _ler_envelope(request: Request) -> dict:
    try:
        corpo = await request.json()
    except Exception:
        raise _invalido("Corpo JSON inválido ou ausente.")
    if not isinstance(corpo, dict):
        raise _invalido("O corpo deve ser um objeto JSON.")
    for campo in ("id", "tipo", "ocorrido_em", "dados"):
        if campo not in corpo:
            raise _invalido(f"Envelope sem o campo '{campo}'.")
    eid = corpo["id"]
    if not isinstance(eid, (str, int)) or isinstance(eid, bool):
        raise _invalido("Campo 'id' inválido.")
    if not isinstance(corpo["dados"], dict):
        raise _invalido("Campo 'dados' inválido.")
    return corpo


@app.post("/eventos")
async def consumir(request: Request):
    env = await _ler_envelope(request)
    eid = str(env["id"])
    tipo = env["tipo"]
    dados = env["dados"]
    with _lock:
        if eid in _eventos_processados:
            return JSONResponse(status_code=200, content={"processado": False})
        if tipo not in TIPOS_CONHECIDOS:
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
        else:
            codigo = dados.get("codigo_rastreio")
            r = _rastreamentos.get(codigo)
            if r is None:
                return JSONResponse(status_code=200, content={"processado": False})
            r["historico"].append({
                "status": dados.get("status"),
                "data_hora": dados.get("data_hora"),
                "local": dados.get("local"),
            })
        _eventos_processados.add(eid)
    return JSONResponse(status_code=200, content={"processado": True})


@app.get("/rastreamento/{codigo}")
async def consultar(codigo: str, request: Request):
    data_consulta = None
    if "data" in request.query_params:
        valor = request.query_params["data"]
        if not DATA_RE.fullmatch(valor):
            raise _invalido("Parâmetro 'data' inválido.")
        try:
            data_consulta = date.fromisoformat(valor)
        except ValueError:
            raise _invalido("Parâmetro 'data' inválido.")
    with _lock:
        r = _rastreamentos.get(codigo)
        if r is None:
            raise ErroAPI(404, "RASTREIO_NAO_ENCONTRADO", "Rastreamento não encontrado.")
        status = r["historico"][-1]["status"]
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
            "historico": copy.deepcopy(r["historico"]),
        }
    return JSONResponse(status_code=200, content=resposta)


@app.post("/admin/reset")
async def reset():
    with _lock:
        _rastreamentos.clear()
        _eventos_processados.clear()
    return Response(status_code=204)
