# arquivo: servicos/notificacoes/main.py
import copy
import threading
from typing import Any

from fastapi import Body, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

CAMPOS_ENVELOPE = ("id", "tipo", "ocorrido_em", "dados")
MENSAGENS = {
    "CRIADO": "Pedido {pedido_id}: envio criado. Rastreio: {codigo_rastreio}.",
    "SAIU_PARA_ENTREGA": "Pedido {pedido_id}: saiu para entrega.",
    "ENTREGUE": "Pedido {pedido_id}: entregue.",
    "FALHA_ENTREGA": "Pedido {pedido_id}: tentativa de entrega sem sucesso.",
    "DEVOLVIDO": "Pedido {pedido_id}: devolvido ao remetente.",
    "CANCELADO": "Pedido {pedido_id}: envio cancelado.",
}

_lock = threading.RLock()
_notificacoes: list = []
_eventos_processados: set = set()
_contador = {"notificacao": 0}


class ErroAPI(Exception):
    def __init__(self, status: int, erro: str, mensagem: str):
        super().__init__(mensagem)
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
        return _erro(404, "NAO_ENCONTRADO", "Recurso não encontrado.")
    if exc.status_code == 405:
        return _erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return _erro(exc.status_code, "ERRO_HTTP", str(exc.detail))


def _invalido(msg: str) -> ErroAPI:
    return ErroAPI(422, "DADOS_INVALIDOS", msg)


def _chave_evento(eid: Any):
    return (type(eid).__name__, repr(eid))


def _registrar(tipo_notif: str, dados: dict, ocorrido_em: Any) -> None:
    pedido_id = dados.get("pedido_id")
    codigo = dados.get("codigo_rastreio")
    _contador["notificacao"] += 1
    _notificacoes.append({
        "id": f"NOT-{_contador['notificacao']:06d}",
        "email": dados.get("destinatario_email"),
        "pedido_id": pedido_id,
        "codigo_rastreio": codigo,
        "tipo": tipo_notif,
        "mensagem": MENSAGENS[tipo_notif].format(pedido_id=pedido_id, codigo_rastreio=codigo),
        "criada_em": ocorrido_em,
    })


@app.post("/eventos")
def consumir_evento(corpo: Any = Body(default=None)):
    if not isinstance(corpo, dict) or any(c not in corpo for c in CAMPOS_ENVELOPE):
        raise _invalido("Envelope de evento incompleto.")
    dados = corpo["dados"]
    if not isinstance(dados, dict):
        raise _invalido("Campo 'dados' inválido.")
    chave = _chave_evento(corpo["id"])
    tipo = corpo["tipo"]

    with _lock:
        if chave in _eventos_processados:
            return {"processado": False}
        if tipo == "envio.criado":
            _registrar("CRIADO", dados, corpo["ocorrido_em"])
        elif tipo == "envio.status_atualizado":
            status = dados.get("status")
            if isinstance(status, str) and status in MENSAGENS and status != "CRIADO":
                _registrar(status, dados, corpo["ocorrido_em"])
        else:
            return {"processado": False}
        _eventos_processados.add(chave)
        return {"processado": True}


@app.get("/notificacoes")
def listar_notificacoes(request: Request):
    email = request.query_params.get("email")
    if email is None:
        raise _invalido("Parâmetro 'email' é obrigatório.")
    with _lock:
        itens = [copy.deepcopy(n) for n in _notificacoes if n["email"] == email]
    itens.sort(key=lambda n: n["id"])
    return itens


@app.post("/admin/reset", status_code=204)
def reset():
    with _lock:
        _notificacoes.clear()
        _eventos_processados.clear()
        _contador["notificacao"] = 0
    return Response(status_code=204)
