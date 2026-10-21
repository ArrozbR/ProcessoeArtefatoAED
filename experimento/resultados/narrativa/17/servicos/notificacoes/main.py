# arquivo: servicos/notificacoes/main.py
import copy

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, StrictStr
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

MENSAGENS_STATUS = {
    "SAIU_PARA_ENTREGA": "Pedido {pedido_id}: saiu para entrega.",
    "ENTREGUE": "Pedido {pedido_id}: entregue.",
    "FALHA_ENTREGA": "Pedido {pedido_id}: tentativa de entrega sem sucesso.",
    "DEVOLVIDO": "Pedido {pedido_id}: devolvido ao remetente.",
    "CANCELADO": "Pedido {pedido_id}: envio cancelado.",
}

_estado = {"contador": 0, "processados": set(), "notificacoes": []}


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


def _registrar(email, pedido_id, codigo, tipo, mensagem, criada_em):
    numero = _estado["contador"] + 1
    _estado["contador"] = numero
    _estado["notificacoes"].append(
        {
            "id": f"NOT-{numero:06d}",
            "email": email,
            "pedido_id": pedido_id,
            "codigo_rastreio": codigo,
            "tipo": tipo,
            "mensagem": mensagem,
            "criada_em": criada_em,
        }
    )


@app.post("/admin/reset", status_code=204)
def reset():
    _estado["contador"] = 0
    _estado["processados"].clear()
    _estado["notificacoes"].clear()
    return Response(status_code=204)


@app.post("/eventos")
def receber_evento(evento: Evento):
    if evento.id in _estado["processados"]:
        return {"processado": False}
    dados = evento.dados

    if evento.tipo == "envio.criado":
        try:
            pedido_id = dados["pedido_id"]
            codigo = dados["codigo_rastreio"]
            email = dados["destinatario_email"]
        except (KeyError, TypeError):
            raise ErroAPI(422, "DADOS_INVALIDOS", "Dados do evento inválidos.")
        mensagem = f"Pedido {pedido_id}: envio criado. Rastreio: {codigo}."
        _registrar(email, pedido_id, codigo, "CRIADO", mensagem, evento.ocorrido_em)
        _estado["processados"].add(evento.id)
        return {"processado": True}

    if evento.tipo == "envio.status_atualizado":
        try:
            pedido_id = dados["pedido_id"]
            codigo = dados["codigo_rastreio"]
            email = dados["destinatario_email"]
            status = dados["status"]
        except (KeyError, TypeError):
            raise ErroAPI(422, "DADOS_INVALIDOS", "Dados do evento inválidos.")
        modelo = MENSAGENS_STATUS.get(status)
        if modelo is not None:
            _registrar(email, pedido_id, codigo, status, modelo.format(pedido_id=pedido_id), evento.ocorrido_em)
        _estado["processados"].add(evento.id)
        return {"processado": True}

    return {"processado": False}


@app.get("/notificacoes")
def listar_notificacoes(email: str = Query(...)):
    itens = [n for n in _estado["notificacoes"] if n["email"] == email]
    itens.sort(key=lambda n: n["id"])
    return copy.deepcopy(itens)
