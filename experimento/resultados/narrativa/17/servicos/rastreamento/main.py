# arquivo: servicos/rastreamento/main.py
import copy
import re
from datetime import date
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, StrictStr
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

DATA_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
FINAIS = {"ENTREGUE", "CANCELADO", "DEVOLVIDO"}

_estado = {"processados": set(), "rastreamentos": {}}


class ErroAPI(Exception):
    def __init__(self, status: int, codigo: str, mensagem: str):
        self.status = status
        self.codigo = codigo
        self.mensagem = mensagem


def _erro(status, codigo, mensagem):
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request: Request, exc: ErroAPI):
    return _erro(exc.status, exc.codigo, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request: Request, exc: RequestValidationError):
    return _erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return _erro(404, "NAO_ENCONTRADO", "Recurso não encontrado.")
    if exc.status_code == 405:
        return _erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return _erro(exc.status_code, "ERRO", str(exc.detail))


class Evento(BaseModel):
    id: StrictStr
    tipo: StrictStr
    ocorrido_em: StrictStr
    dados: dict


def _invalido():
    return ErroAPI(422, "DADOS_INVALIDOS", "Dados do evento inválidos.")


@app.post("/admin/reset", status_code=204)
def reset():
    _estado["processados"].clear()
    _estado["rastreamentos"].clear()
    return Response(status_code=204)


@app.post("/eventos")
def receber_evento(evento: Evento):
    if evento.id in _estado["processados"]:
        return {"processado": False}
    dados = evento.dados

    if evento.tipo == "envio.criado":
        try:
            codigo = dados["codigo_rastreio"]
            registro = {
                "codigo_rastreio": codigo,
                "envio_id": dados["envio_id"],
                "pedido_id": dados["pedido_id"],
                "transportadora_id": dados["transportadora_id"],
                "data_prevista_entrega": dados["data_prevista_entrega"],
                "historico": [{"status": "CRIADO", "data_hora": dados["data_hora"], "local": None}],
            }
        except (KeyError, TypeError):
            raise _invalido()
        _estado["rastreamentos"][codigo] = registro
        _estado["processados"].add(evento.id)
        return {"processado": True}

    if evento.tipo == "envio.status_atualizado":
        try:
            codigo = dados["codigo_rastreio"]
            item = {"status": dados["status"], "data_hora": dados["data_hora"], "local": dados.get("local")}
        except (KeyError, TypeError):
            raise _invalido()
        registro = _estado["rastreamentos"].get(codigo)
        if registro is None:
            return {"processado": False}
        registro["historico"].append(item)
        _estado["processados"].add(evento.id)
        return {"processado": True}

    return {"processado": False}


@app.get("/rastreamento/{codigo_rastreio}")
def obter_rastreamento(codigo_rastreio: str, data: Optional[str] = None):
    data_ref = None
    if data is not None:
        if not DATA_RE.match(data):
            raise ErroAPI(422, "DADOS_INVALIDOS", "Data inválida.")
        try:
            data_ref = date.fromisoformat(data)
        except ValueError:
            raise ErroAPI(422, "DADOS_INVALIDOS", "Data inválida.")

    registro = _estado["rastreamentos"].get(codigo_rastreio)
    if registro is None:
        raise ErroAPI(404, "RASTREIO_NAO_ENCONTRADO", "Código de rastreio não encontrado.")

    status = registro["historico"][-1]["status"]
    atrasado = False
    if data_ref is not None and status not in FINAIS:
        atrasado = data_ref > date.fromisoformat(registro["data_prevista_entrega"])

    return {
        "codigo_rastreio": registro["codigo_rastreio"],
        "envio_id": registro["envio_id"],
        "pedido_id": registro["pedido_id"],
        "transportadora_id": registro["transportadora_id"],
        "data_prevista_entrega": registro["data_prevista_entrega"],
        "status": status,
        "entregue": status == "ENTREGUE",
        "atrasado": atrasado,
        "historico": copy.deepcopy(registro["historico"]),
    }
