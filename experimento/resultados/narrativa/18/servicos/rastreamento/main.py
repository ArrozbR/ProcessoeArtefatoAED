# arquivo: servicos/rastreamento/main.py
import copy
import re
import threading
from datetime import datetime
from typing import Any, Optional

from fastapi import Body, FastAPI, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()


class ErroAPI(Exception):
    def __init__(self, status: int, erro: str, mensagem: str):
        super().__init__(mensagem)
        self.status = status
        self.erro = erro
        self.mensagem = mensagem


def _resp_erro(status: int, erro: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": erro, "mensagem": mensagem})


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request, exc: ErroAPI):
    return _resp_erro(exc.status, exc.erro, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request, exc):
    return _resp_erro(422, "DADOS_INVALIDOS", "Dados invalidos.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request, exc: StarletteHTTPException):
    codigos = {404: "NAO_ENCONTRADO", 405: "METODO_NAO_PERMITIDO"}
    return _resp_erro(exc.status_code, codigos.get(exc.status_code, "ERRO"), str(exc.detail))


def _invalido(msg: str = "Dados invalidos.") -> None:
    raise ErroAPI(422, "DADOS_INVALIDOS", msg)


RE_DATA = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
STATUS_FINAIS = {"ENTREGUE", "CANCELADO", "DEVOLVIDO"}
TIPOS_CONHECIDOS = {"envio.criado", "envio.status_atualizado"}

_lock = threading.Lock()
_rastreamentos: dict = {}
_eventos_processados: set = set()


@app.post("/admin/reset", status_code=204)
def reset():
    with _lock:
        _rastreamentos.clear()
        _eventos_processados.clear()
    return Response(status_code=204)


@app.post("/eventos")
def receber_evento(corpo: Any = Body(None)):
    if not isinstance(corpo, dict):
        _invalido("Corpo deve ser um objeto JSON.")
    for campo in ("id", "tipo", "ocorrido_em", "dados"):
        if campo not in corpo or corpo[campo] is None:
            _invalido(f"Campo '{campo}' ausente.")
    dados = corpo["dados"]
    if not isinstance(dados, dict):
        _invalido("Campo 'dados' invalido.")
    eid = corpo["id"]
    try:
        chave = eid if isinstance(eid, (str, int, float, bool)) else repr(eid)
    except Exception:
        chave = repr(eid)
    tipo = corpo["tipo"]

    with _lock:
        if chave in _eventos_processados or tipo not in TIPOS_CONHECIDOS:
            return {"processado": False}

        if tipo == "envio.criado":
            codigo = dados.get("codigo_rastreio")
            if not isinstance(codigo, str):
                return {"processado": False}
            if codigo not in _rastreamentos:
                _rastreamentos[codigo] = {
                    "codigo_rastreio": codigo,
                    "envio_id": dados.get("envio_id"),
                    "pedido_id": dados.get("pedido_id"),
                    "transportadora_id": dados.get("transportadora_id"),
                    "data_prevista_entrega": dados.get("data_prevista_entrega"),
                    "historico": [{
                        "status": "CRIADO",
                        "data_hora": dados.get("data_hora", corpo["ocorrido_em"]),
                        "local": None,
                    }],
                }
            _eventos_processados.add(chave)
            return {"processado": True}

        codigo = dados.get("codigo_rastreio")
        r = _rastreamentos.get(codigo) if isinstance(codigo, str) else None
        if r is None:
            return {"processado": False}
        r["historico"].append({
            "status": dados.get("status"),
            "data_hora": dados.get("data_hora", corpo["ocorrido_em"]),
            "local": dados.get("local"),
        })
        _eventos_processados.add(chave)
        return {"processado": True}


@app.get("/rastreamento/{codigo}")
def obter_rastreamento(codigo: str, data: Optional[str] = Query(None)):
    data_consulta = None
    if data is not None:
        if not RE_DATA.match(data):
            _invalido("Parametro 'data' invalido.")
        try:
            data_consulta = datetime.strptime(data, "%Y-%m-%d").date()
        except ValueError:
            _invalido("Parametro 'data' invalido.")

    with _lock:
        r = _rastreamentos.get(codigo)
        if r is None:
            raise ErroAPI(404, "RASTREIO_NAO_ENCONTRADO", "Codigo de rastreio nao encontrado.")
        r = copy.deepcopy(r)

    status = r["historico"][-1]["status"]
    atrasado = False
    if data_consulta is not None and status not in STATUS_FINAIS:
        prevista = r.get("data_prevista_entrega")
        if isinstance(prevista, str):
            try:
                atrasado = data_consulta > datetime.strptime(prevista, "%Y-%m-%d").date()
            except ValueError:
                atrasado = False
    return {
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
