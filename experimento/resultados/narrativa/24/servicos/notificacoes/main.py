# arquivo: servicos/notificacoes/main.py
import copy
import json
import threading

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

TIPOS_CONHECIDOS = {"envio.criado", "envio.status_atualizado"}

MENSAGENS_STATUS = {
    "SAIU_PARA_ENTREGA": "Pedido {pedido_id}: saiu para entrega.",
    "ENTREGUE": "Pedido {pedido_id}: entregue.",
    "FALHA_ENTREGA": "Pedido {pedido_id}: tentativa de entrega sem sucesso.",
    "DEVOLVIDO": "Pedido {pedido_id}: devolvido ao remetente.",
    "CANCELADO": "Pedido {pedido_id}: envio cancelado.",
}


class ErroAPI(Exception):
    def __init__(self, status: int, codigo: str, mensagem: str):
        self.status = status
        self.codigo = codigo
        self.mensagem = mensagem


@app.exception_handler(ErroAPI)
async def _erro_api(request: Request, exc: ErroAPI):
    return JSONResponse(status_code=exc.status, content={"erro": exc.codigo, "mensagem": exc.mensagem})


@app.exception_handler(RequestValidationError)
async def _erro_validacao(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={"erro": "DADOS_INVALIDOS", "mensagem": "Dados inválidos."})


@app.exception_handler(StarletteHTTPException)
async def _erro_http(request: Request, exc: StarletteHTTPException):
    codigo = "NAO_ENCONTRADO" if exc.status_code == 404 else "ERRO_HTTP"
    return JSONResponse(status_code=exc.status_code, content={"erro": codigo, "mensagem": str(exc.detail)})


_lock = threading.Lock()
_notificacoes = []
_eventos_processados = set()
_contador = [0]


def _invalido():
    return ErroAPI(422, "DADOS_INVALIDOS", "Dados inválidos.")


def _registrar(email, pedido_id, codigo, tipo, mensagem, criada_em):
    _contador[0] += 1
    _notificacoes.append({
        "id": "NOT-%06d" % _contador[0],
        "email": email,
        "pedido_id": pedido_id,
        "codigo_rastreio": codigo,
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
        corpo = json.loads(await request.body())
    except Exception:
        raise _invalido()
    if not isinstance(corpo, dict):
        raise _invalido()
    for campo in ("id", "tipo", "ocorrido_em", "dados"):
        if corpo.get(campo) is None:
            raise _invalido()
    if not isinstance(corpo["dados"], dict):
        raise _invalido()
    try:
        id_evento = corpo["id"] if isinstance(corpo["id"], str) else json.dumps(corpo["id"], sort_keys=True)
    except Exception:
        raise _invalido()
    tipo = corpo["tipo"]
    dados = corpo["dados"]
    ocorrido_em = corpo["ocorrido_em"]

    with _lock:
        if id_evento in _eventos_processados or tipo not in TIPOS_CONHECIDOS:
            return {"processado": False}

        pedido_id = dados.get("pedido_id")
        codigo = dados.get("codigo_rastreio")
        email = dados.get("destinatario_email")

        if tipo == "envio.criado":
            mensagem = "Pedido {}: envio criado. Rastreio: {}.".format(pedido_id, codigo)
            _registrar(email, pedido_id, codigo, "CRIADO", mensagem, ocorrido_em)
        else:
            status = dados.get("status")
            modelo = MENSAGENS_STATUS.get(status) if isinstance(status, str) else None
            if modelo is not None:
                _registrar(email, pedido_id, codigo, status, modelo.format(pedido_id=pedido_id), ocorrido_em)

        _eventos_processados.add(id_evento)
        return {"processado": True}


@app.get("/notificacoes")
def listar(email: str = Query(...)):
    with _lock:
        itens = [n for n in _notificacoes if n["email"] == email]
        itens.sort(key=lambda n: n["id"])
        return copy.deepcopy(itens)
