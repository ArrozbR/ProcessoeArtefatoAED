# arquivo: servicos/notificacoes/main.py
import copy
import threading

from fastapi import FastAPI, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, StrictStr
from starlette.exceptions import HTTPException as StarletteHTTPException


class ErroAPI(Exception):
    def __init__(self, status: int, codigo: str, mensagem: str):
        super().__init__(mensagem)
        self.status = status
        self.codigo = codigo
        self.mensagem = mensagem


def _resposta_erro(status: int, codigo: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


app = FastAPI()


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request, exc: ErroAPI):
    return _resposta_erro(exc.status, exc.codigo, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request, exc: RequestValidationError):
    return _resposta_erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request, exc: StarletteHTTPException):
    codigos = {404: "NAO_ENCONTRADO", 405: "METODO_NAO_PERMITIDO"}
    return _resposta_erro(exc.status_code, codigos.get(exc.status_code, "ERRO"), str(exc.detail))


MENSAGENS_STATUS = {
    "SAIU_PARA_ENTREGA": "Pedido {pedido_id}: saiu para entrega.",
    "ENTREGUE": "Pedido {pedido_id}: entregue.",
    "FALHA_ENTREGA": "Pedido {pedido_id}: tentativa de entrega sem sucesso.",
    "DEVOLVIDO": "Pedido {pedido_id}: devolvido ao remetente.",
    "CANCELADO": "Pedido {pedido_id}: envio cancelado.",
}

_trava = threading.RLock()
_estado = {"contador": 0, "notificacoes": [], "eventos_processados": set()}


class Evento(BaseModel):
    id: StrictStr
    tipo: StrictStr
    ocorrido_em: StrictStr
    dados: dict


def _registrar(dados: dict, tipo: str, mensagem: str, criada_em: str) -> None:
    numero = _estado["contador"] + 1
    _estado["contador"] = numero
    _estado["notificacoes"].append({
        "id": f"NOT-{numero:06d}",
        "email": dados.get("destinatario_email"),
        "pedido_id": dados.get("pedido_id"),
        "codigo_rastreio": dados.get("codigo_rastreio"),
        "tipo": tipo,
        "mensagem": mensagem,
        "criada_em": criada_em,
    })


@app.post("/admin/reset")
def reset():
    with _trava:
        _estado["contador"] = 0
        _estado["notificacoes"] = []
        _estado["eventos_processados"] = set()
    return Response(status_code=204)


@app.post("/eventos")
def receber_evento(evento: Evento):
    with _trava:
        if evento.id in _estado["eventos_processados"]:
            return JSONResponse(status_code=200, content={"processado": False})
        dados = evento.dados
        pedido_id = dados.get("pedido_id")
        if evento.tipo == "envio.criado":
            mensagem = f"Pedido {pedido_id}: envio criado. Rastreio: {dados.get('codigo_rastreio')}."
            _registrar(dados, "CRIADO", mensagem, evento.ocorrido_em)
        elif evento.tipo == "envio.status_atualizado":
            status = dados.get("status")
            modelo = MENSAGENS_STATUS.get(status) if isinstance(status, str) else None
            if modelo is not None:
                _registrar(dados, status, modelo.format(pedido_id=pedido_id), evento.ocorrido_em)
        else:
            return JSONResponse(status_code=200, content={"processado": False})
        _estado["eventos_processados"].add(evento.id)
        return JSONResponse(status_code=200, content={"processado": True})


@app.get("/notificacoes")
def listar_notificacoes(email: str = Query(...)):
    with _trava:
        lista = [copy.deepcopy(n) for n in _estado["notificacoes"] if n["email"] == email]
    lista.sort(key=lambda n: n["id"])
    return JSONResponse(status_code=200, content=lista)
