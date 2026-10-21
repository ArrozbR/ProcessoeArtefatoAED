# arquivo: servicos/notificacoes/main.py
"""Microsserviço de Notificações."""
import copy
import json
import threading

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

MENSAGENS_STATUS = {
    "SAIU_PARA_ENTREGA": "Pedido {pedido_id}: saiu para entrega.",
    "ENTREGUE": "Pedido {pedido_id}: entregue.",
    "FALHA_ENTREGA": "Pedido {pedido_id}: tentativa de entrega sem sucesso.",
    "DEVOLVIDO": "Pedido {pedido_id}: devolvido ao remetente.",
    "CANCELADO": "Pedido {pedido_id}: envio cancelado.",
}
MENSAGEM_CRIADO = "Pedido {pedido_id}: envio criado. Rastreio: {codigo_rastreio}."

_lock = threading.Lock()
_notificacoes: list = []
_eventos_processados: set = set()
_contador = {"valor": 0}


class ErroAPI(Exception):
    def __init__(self, status: int, codigo: str, mensagem: str):
        self.status = status
        self.codigo = codigo
        self.mensagem = mensagem


def _erro(status: int, codigo: str, mensagem: str) -> JSONResponse:
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


def _invalido() -> ErroAPI:
    return ErroAPI(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.post("/admin/reset", status_code=204)
def reset():
    with _lock:
        _notificacoes.clear()
        _eventos_processados.clear()
        _contador["valor"] = 0
    return Response(status_code=204)


def _registrar(email, pedido_id, codigo_rastreio, tipo, mensagem, criada_em) -> None:
    _contador["valor"] += 1
    _notificacoes.append(
        {
            "id": f"NOT-{_contador['valor']:06d}",
            "email": email,
            "pedido_id": pedido_id,
            "codigo_rastreio": codigo_rastreio,
            "tipo": tipo,
            "mensagem": mensagem,
            "criada_em": criada_em,
        }
    )


@app.post("/eventos")
async def receber_evento(request: Request):
    try:
        corpo = json.loads(await request.body())
    except Exception:
        raise _invalido()
    if not isinstance(corpo, dict):
        raise _invalido()
    for campo in ("id", "tipo", "ocorrido_em", "dados"):
        if campo not in corpo or corpo[campo] is None:
            raise _invalido()
    if not isinstance(corpo["dados"], dict):
        raise _invalido()

    tipo = corpo["tipo"]
    dados = corpo["dados"]
    ocorrido_em = corpo["ocorrido_em"]
    try:
        chave = json.dumps(corpo["id"], sort_keys=True)
    except Exception:
        raise _invalido()

    with _lock:
        if chave in _eventos_processados:
            return {"processado": False}

        if tipo == "envio.criado":
            try:
                pedido_id = dados["pedido_id"]
                codigo = dados["codigo_rastreio"]
                email = dados["destinatario_email"]
            except KeyError:
                raise _invalido()
            mensagem = MENSAGEM_CRIADO.format(pedido_id=pedido_id, codigo_rastreio=codigo)
            _registrar(email, pedido_id, codigo, "CRIADO", mensagem, ocorrido_em)
            _eventos_processados.add(chave)
            return {"processado": True}

        if tipo == "envio.status_atualizado":
            try:
                status = dados["status"]
                pedido_id = dados["pedido_id"]
                codigo = dados["codigo_rastreio"]
                email = dados["destinatario_email"]
            except KeyError:
                raise _invalido()
            modelo = MENSAGENS_STATUS.get(status) if isinstance(status, str) else None
            if modelo is not None:
                mensagem = modelo.format(pedido_id=pedido_id)
                _registrar(email, pedido_id, codigo, status, mensagem, ocorrido_em)
            _eventos_processados.add(chave)
            return {"processado": True}

        return {"processado": False}


@app.get("/notificacoes")
def listar_notificacoes(email: str = Query(...)):
    with _lock:
        itens = [copy.deepcopy(n) for n in _notificacoes if n["email"] == email]
    itens.sort(key=lambda n: n["id"])
    return itens
