# arquivo: servicos/notificacoes/main.py
import copy
import threading

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

MENSAGENS_STATUS = {
    "SAIU_PARA_ENTREGA": "Pedido {pedido_id}: saiu para entrega.",
    "ENTREGUE": "Pedido {pedido_id}: entregue.",
    "FALHA_ENTREGA": "Pedido {pedido_id}: tentativa de entrega sem sucesso.",
    "DEVOLVIDO": "Pedido {pedido_id}: devolvido ao remetente.",
    "CANCELADO": "Pedido {pedido_id}: envio cancelado.",
}
MENSAGEM_CRIADO = "Pedido {pedido_id}: envio criado. Rastreio: {codigo_rastreio}."


class ErroAPI(Exception):
    def __init__(self, status: int, codigo: str, mensagem: str):
        super().__init__(mensagem)
        self.status = status
        self.codigo = codigo
        self.mensagem = mensagem


app = FastAPI()


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request: Request, exc: ErroAPI):
    return JSONResponse(status_code=exc.status, content={"erro": exc.codigo, "mensagem": exc.mensagem})


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={"erro": "DADOS_INVALIDOS", "mensagem": "Dados inválidos."})


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request: Request, exc: StarletteHTTPException):
    codigo = "NAO_ENCONTRADO" if exc.status_code == 404 else "ERRO_HTTP"
    return JSONResponse(status_code=exc.status_code, content={"erro": codigo, "mensagem": str(exc.detail)})


_lock = threading.Lock()
_notificacoes = []
_eventos_processados = set()
_contador = [0]


def _dados_invalidos():
    return ErroAPI(422, "DADOS_INVALIDOS", "Dados inválidos.")


def _registrar(email, pedido_id, codigo_rastreio, tipo, mensagem, criada_em):
    _contador[0] += 1
    _notificacoes.append({
        "id": "NOT-%06d" % _contador[0],
        "email": email,
        "pedido_id": pedido_id,
        "codigo_rastreio": codigo_rastreio,
        "tipo": tipo,
        "mensagem": mensagem,
        "criada_em": criada_em,
    })


@app.post("/admin/reset", status_code=204)
def reset():
    with _lock:
        _notificacoes.clear()
        _eventos_processados.clear()
        _contador[0] = 0
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
        if corpo.get(campo) is None:
            raise _dados_invalidos()
    evento_id = corpo["id"]
    tipo = corpo["tipo"]
    ocorrido_em = corpo["ocorrido_em"]
    dados = corpo["dados"]
    if not isinstance(evento_id, str) or not isinstance(tipo, str) or not isinstance(dados, dict):
        raise _dados_invalidos()

    with _lock:
        if evento_id in _eventos_processados:
            return {"processado": False}

        pedido_id = dados.get("pedido_id")
        codigo = dados.get("codigo_rastreio")
        email = dados.get("destinatario_email")

        if tipo == "envio.criado":
            mensagem = MENSAGEM_CRIADO.format(pedido_id=pedido_id, codigo_rastreio=codigo)
            _registrar(email, pedido_id, codigo, "CRIADO", mensagem, ocorrido_em)
            _eventos_processados.add(evento_id)
            return {"processado": True}

        if tipo == "envio.status_atualizado":
            status = dados.get("status")
            modelo = MENSAGENS_STATUS.get(status) if isinstance(status, str) else None
            if modelo is not None:
                _registrar(email, pedido_id, codigo, status, modelo.format(pedido_id=pedido_id), ocorrido_em)
            _eventos_processados.add(evento_id)
            return {"processado": True}

        return {"processado": False}


@app.get("/notificacoes")
def listar_notificacoes(email: str = Query(...)):
    with _lock:
        return [copy.deepcopy(n) for n in _notificacoes if n["email"] == email]
