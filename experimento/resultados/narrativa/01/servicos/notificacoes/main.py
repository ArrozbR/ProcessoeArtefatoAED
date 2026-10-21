# arquivo: servicos/notificacoes/main.py
import copy
import json
import threading

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException


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
    codigos = {404: "NAO_ENCONTRADO", 405: "METODO_NAO_PERMITIDO"}
    return JSONResponse(
        status_code=exc.status_code,
        content={"erro": codigos.get(exc.status_code, "ERRO"), "mensagem": str(exc.detail)},
    )


def _invalido():
    return ErroAPI(422, "DADOS_INVALIDOS", "Dados inválidos.")


MENSAGENS_STATUS = {
    "SAIU_PARA_ENTREGA": "Pedido {pedido_id}: saiu para entrega.",
    "ENTREGUE": "Pedido {pedido_id}: entregue.",
    "FALHA_ENTREGA": "Pedido {pedido_id}: tentativa de entrega sem sucesso.",
    "DEVOLVIDO": "Pedido {pedido_id}: devolvido ao remetente.",
    "CANCELADO": "Pedido {pedido_id}: envio cancelado.",
}

_lock = threading.Lock()
_notificacoes: list = []
_eventos_processados: set = set()
_contador = {"valor": 0}


@app.post("/admin/reset", status_code=204)
def reset():
    with _lock:
        _notificacoes.clear()
        _eventos_processados.clear()
        _contador["valor"] = 0
    return Response(status_code=204)


def _registrar(email, pedido_id, codigo, tipo, mensagem, criada_em):
    _contador["valor"] += 1
    _notificacoes.append(
        {
            "id": "NOT-%06d" % _contador["valor"],
            "email": email,
            "pedido_id": pedido_id,
            "codigo_rastreio": codigo,
            "tipo": tipo,
            "mensagem": mensagem,
            "criada_em": criada_em,
        }
    )


@app.post("/eventos")
async def receber_evento(request: Request):
    corpo = await request.body()
    try:
        evento = json.loads(corpo)
    except Exception:
        raise _invalido()
    if not isinstance(evento, dict):
        raise _invalido()
    for campo in ("id", "tipo", "ocorrido_em", "dados"):
        if campo not in evento or evento[campo] is None:
            raise _invalido()
    dados = evento["dados"]
    if not isinstance(dados, dict):
        raise _invalido()

    evento_id = evento["id"]
    tipo = evento["tipo"]
    ocorrido_em = evento["ocorrido_em"]
    with _lock:
        if not isinstance(evento_id, (str, int)) or evento_id in _eventos_processados:
            return {"processado": False}

        if tipo == "envio.criado":
            pedido_id = dados.get("pedido_id")
            codigo = dados.get("codigo_rastreio")
            _registrar(
                dados.get("destinatario_email"),
                pedido_id,
                codigo,
                "CRIADO",
                "Pedido {}: envio criado. Rastreio: {}.".format(pedido_id, codigo),
                ocorrido_em,
            )
            _eventos_processados.add(evento_id)
            return {"processado": True}

        if tipo == "envio.status_atualizado":
            status = dados.get("status")
            modelo = MENSAGENS_STATUS.get(status)
            if modelo is not None:
                pedido_id = dados.get("pedido_id")
                _registrar(
                    dados.get("destinatario_email"),
                    pedido_id,
                    dados.get("codigo_rastreio"),
                    status,
                    modelo.format(pedido_id=pedido_id),
                    ocorrido_em,
                )
            _eventos_processados.add(evento_id)
            return {"processado": True}

        return {"processado": False}


@app.get("/notificacoes")
def listar_notificacoes(email: str = Query(...)):
    with _lock:
        itens = [copy.deepcopy(n) for n in _notificacoes if n["email"] == email]
    itens.sort(key=lambda n: n["id"])
    return itens
