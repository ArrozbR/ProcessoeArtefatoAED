# arquivo: servicos/notificacoes/main.py
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
_notificacoes = []
_eventos_processados = set()
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
    return _erro(422, "DADOS_INVALIDOS", "Dados invalidos na requisicao.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return _erro(404, "NAO_ENCONTRADO", "Recurso nao encontrado.")
    if exc.status_code == 405:
        return _erro(405, "METODO_NAO_PERMITIDO", "Metodo nao permitido.")
    return _erro(exc.status_code, "ERRO", str(exc.detail))


def _dados_invalidos():
    return ErroAPI(422, "DADOS_INVALIDOS", "Dados invalidos na requisicao.")


def _criar_notificacao(email, pedido_id, codigo_rastreio, tipo, mensagem, criada_em):
    _contador["valor"] += 1
    _notificacoes.append({
        "id": "NOT-%06d" % _contador["valor"],
        "email": email,
        "pedido_id": pedido_id,
        "codigo_rastreio": codigo_rastreio,
        "tipo": tipo,
        "mensagem": mensagem,
        "criada_em": criada_em,
    })


@app.post("/admin/reset")
def reset():
    with _lock:
        _notificacoes.clear()
        _eventos_processados.clear()
        _contador["valor"] = 0
    return Response(status_code=204)


@app.post("/eventos")
async def receber_evento(request: Request):
    try:
        corpo = await request.json()
    except Exception:
        raise _dados_invalidos()
    if not isinstance(corpo, dict):
        raise _dados_invalidos()
    for campo in ("id", "tipo", "ocorrido_em", "dados"):
        if campo not in corpo or corpo[campo] is None:
            raise _dados_invalidos()
    dados = corpo["dados"]
    if not isinstance(dados, dict):
        raise _dados_invalidos()
    evento_id = corpo["id"]
    tipo = corpo["tipo"]
    ocorrido_em = corpo["ocorrido_em"]
    try:
        hash(evento_id)
    except TypeError:
        raise _dados_invalidos()

    with _lock:
        if evento_id in _eventos_processados:
            return {"processado": False}
        pedido_id = dados.get("pedido_id")
        codigo = dados.get("codigo_rastreio")
        email = dados.get("destinatario_email")
        if tipo == "envio.criado":
            _criar_notificacao(
                email, pedido_id, codigo, "CRIADO",
                MENSAGEM_CRIADO.format(pedido_id=pedido_id, codigo_rastreio=codigo),
                ocorrido_em,
            )
            _eventos_processados.add(evento_id)
            return {"processado": True}
        if tipo == "envio.status_atualizado":
            status = dados.get("status")
            modelo = MENSAGENS_STATUS.get(status)
            if modelo is not None:
                _criar_notificacao(
                    email, pedido_id, codigo, status,
                    modelo.format(pedido_id=pedido_id),
                    ocorrido_em,
                )
            _eventos_processados.add(evento_id)
            return {"processado": True}
        return {"processado": False}


@app.get("/notificacoes")
def listar_notificacoes(email: str = Query(...)):
    with _lock:
        itens = [dict(n) for n in _notificacoes if n["email"] == email]
    itens.sort(key=lambda n: n["id"])
    return itens
